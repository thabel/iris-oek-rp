"""OGC WFS 2.0 access: transports, capability discovery and paged GetFeature.

Two transports share one interface so the live and fixture paths run the
same discovery, paging and parsing code:

* ``HttpTransport`` talks to a real endpoint with timeouts, a fixed delay
  between requests, bounded retries on 429/5xx (honouring Retry-After) and
  no retries on other 4xx responses, so access restrictions are reported
  instead of worked around.
* ``FixtureTransport`` answers the same requests from files on disk.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Protocol

import requests
from lxml import etree

log = logging.getLogger(__name__)


class WfsError(RuntimeError):
    """The service could not be used (unreachable, refused, or returned an
    OGC exception)."""


@dataclass
class RequestRecord:
    url: str
    status: int | None
    bytes: int
    elapsed_s: float
    error: str | None = None


class Transport(Protocol):
    endpoint: str
    requests_made: list[RequestRecord]

    def get(self, params: dict[str, str]) -> bytes: ...


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class HttpTransport:
    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        endpoint: str,
        *,
        timeout_s: float,
        max_retries: int,
        backoff_s: float,
        request_delay_s: float,
        user_agent: str,
        session: requests.Session | None = None,
    ):
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.request_delay_s = request_delay_s
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.requests_made: list[RequestRecord] = []
        self._last_request = 0.0

    def get(self, params: dict[str, str]) -> bytes:
        attempt = 0
        while True:
            wait = self.request_delay_s - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            started = time.monotonic()
            prepared_url = requests.Request(
                "GET", self.endpoint, params=params
            ).prepare().url
            try:
                response = self.session.get(
                    self.endpoint, params=params, timeout=self.timeout_s
                )
            except requests.RequestException as exc:
                self.requests_made.append(
                    RequestRecord(prepared_url, None, 0,
                                  time.monotonic() - started, repr(exc))
                )
                if attempt >= self.max_retries:
                    raise WfsError(f"Request failed: {exc}") from exc
                attempt += 1
                time.sleep(self.backoff_s * 2 ** (attempt - 1))
                continue

            self.requests_made.append(
                RequestRecord(response.url, response.status_code,
                              len(response.content), time.monotonic() - started)
            )
            if response.status_code in self.RETRY_STATUSES and attempt < self.max_retries:
                attempt += 1
                retry_after = response.headers.get("Retry-After", "")
                delay = (float(retry_after) if retry_after.isdigit()
                         else self.backoff_s * 2 ** (attempt - 1))
                log.warning("HTTP %s from %s, retry %s/%s in %.1fs",
                            response.status_code, response.url, attempt,
                            self.max_retries, delay)
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                raise WfsError(
                    f"HTTP {response.status_code} for {response.url}"
                    + (" (access restricted; not retried)"
                       if response.status_code in (401, 403) else "")
                )
            raise_for_ows_exception(response.content)
            return response.content


class FixtureTransport:
    """Serves WFS responses from a directory.

    Expected files: ``capabilities.xml``, ``describefeaturetype.xsd``,
    ``hits.xml`` and ``getfeature_<startIndex>.xml`` for each page.
    """

    def __init__(self, directory: str | Path, endpoint: str = "fixture://wfs"):
        self.directory = Path(directory)
        self.endpoint = endpoint
        self.requests_made: list[RequestRecord] = []

    def _file_for(self, params: dict[str, str]) -> Path:
        request = params.get("REQUEST", "").lower()
        if request == "getcapabilities":
            return self.directory / "capabilities.xml"
        if request == "describefeaturetype":
            return self.directory / "describefeaturetype.xsd"
        if request == "getfeature":
            if params.get("RESULTTYPE", "").lower() == "hits":
                return self.directory / "hits.xml"
            return self.directory / f"getfeature_{params.get('STARTINDEX', '0')}.xml"
        raise WfsError(f"Fixture transport cannot answer REQUEST={request!r}")

    def get(self, params: dict[str, str]) -> bytes:
        path = self._file_for(params)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        if not path.exists():
            self.requests_made.append(
                RequestRecord(f"{self.endpoint}?{query}", 404, 0, 0.0, "missing fixture")
            )
            raise WfsError(f"No fixture for request: {path.name}")
        content = path.read_bytes()
        self.requests_made.append(
            RequestRecord(f"{self.endpoint}?{query}", 200, len(content), 0.0)
        )
        raise_for_ows_exception(content)
        return content


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_xml(content: bytes) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    try:
        return etree.fromstring(content, parser)
    except etree.XMLSyntaxError as exc:
        snippet = content[:200].decode("utf-8", "replace")
        raise WfsError(f"Response is not XML: {exc}; starts with {snippet!r}") from exc


def _local(tag: str) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def raise_for_ows_exception(content: bytes) -> None:
    head = content[:500].lstrip()
    if b"ExceptionReport" not in head and b"ServiceExceptionReport" not in head:
        return
    root = _parse_xml(content)
    texts = [t.strip() for t in root.xpath("//*[local-name()='ExceptionText']/text()")]
    codes = root.xpath("//*[local-name()='Exception']/@exceptionCode")
    raise WfsError(f"OGC exception {codes or ''}: {'; '.join(texts) or 'no text'}")


def _xtext(node: etree._Element, path: str) -> str | None:
    found = node.xpath(path)
    if not found:
        return None
    value = found[0] if isinstance(found[0], str) else found[0].text
    return value.strip() if value and value.strip() else None


# ---------------------------------------------------------------------------
# Capabilities and schema
# ---------------------------------------------------------------------------


@dataclass
class FeatureTypeInfo:
    name: str
    title: str | None
    default_crs: str | None
    other_crs: list[str]
    wgs84_bbox: tuple[float, float, float, float] | None


@dataclass
class Capabilities:
    version: str | None
    title: str | None
    provider: str | None
    fees: str | None
    access_constraints: str | None
    implements_paging: bool
    count_default: int | None
    feature_types: dict[str, FeatureTypeInfo]
    getfeature_formats: list[str] = field(default_factory=list)

    def find_feature_type(self, type_name: str) -> FeatureTypeInfo | None:
        if type_name in self.feature_types:
            return self.feature_types[type_name]
        local = type_name.split(":")[-1]
        matches = [ft for n, ft in self.feature_types.items()
                   if n.split(":")[-1] == local]
        return matches[0] if len(matches) == 1 else None


def _constraint(root: etree._Element, name: str) -> str | None:
    return _xtext(
        root,
        "//*[local-name()='OperationsMetadata']//*[local-name()='Constraint']"
        f"[@name='{name}']/*[local-name()='DefaultValue']",
    )


def parse_capabilities(content: bytes) -> Capabilities:
    root = _parse_xml(content)
    if _local(root.tag) != "WFS_Capabilities":
        raise WfsError(f"Not a WFS capabilities document (root {_local(root.tag)!r})")

    feature_types: dict[str, FeatureTypeInfo] = {}
    for ft in root.xpath("//*[local-name()='FeatureType']"):
        name = _xtext(ft, "*[local-name()='Name']")
        if not name:
            continue
        bbox = None
        lower = _xtext(ft, ".//*[local-name()='WGS84BoundingBox']/*[local-name()='LowerCorner']")
        upper = _xtext(ft, ".//*[local-name()='WGS84BoundingBox']/*[local-name()='UpperCorner']")
        if lower and upper:
            lx, ly = (float(v) for v in lower.split())
            ux, uy = (float(v) for v in upper.split())
            bbox = (lx, ly, ux, uy)
        feature_types[name] = FeatureTypeInfo(
            name=name,
            title=_xtext(ft, "*[local-name()='Title']"),
            default_crs=_xtext(ft, "*[local-name()='DefaultCRS' or local-name()='DefaultSRS' or local-name()='SRS']"),
            other_crs=[c.strip() for c in ft.xpath(
                "*[local-name()='OtherCRS' or local-name()='OtherSRS']/text()")],
            wgs84_bbox=bbox,
        )

    count_default = _constraint(root, "CountDefault")
    formats = [v.strip() for v in root.xpath(
        "//*[local-name()='Operation'][@name='GetFeature']"
        "//*[local-name()='Parameter'][@name='outputFormat']//*[local-name()='Value']/text()"
    )]
    return Capabilities(
        version=root.get("version"),
        title=_xtext(root, "//*[local-name()='ServiceIdentification']/*[local-name()='Title']"),
        provider=_xtext(root, "//*[local-name()='ServiceProvider']/*[local-name()='ProviderName']"),
        fees=_xtext(root, "//*[local-name()='ServiceIdentification']/*[local-name()='Fees']"),
        access_constraints=_xtext(
            root, "//*[local-name()='ServiceIdentification']/*[local-name()='AccessConstraints']"),
        implements_paging=(_constraint(root, "ImplementsResultPaging") or "").upper() == "TRUE",
        count_default=int(count_default) if count_default and count_default.isdigit() else None,
        feature_types=feature_types,
        getfeature_formats=formats,
    )


@dataclass
class FeatureSchema:
    type_name: str
    attributes: dict[str, str]  # name -> XSD type
    geometry_property: str | None


def parse_describe_feature_type(content: bytes, type_name: str) -> FeatureSchema:
    root = _parse_xml(content)
    local = type_name.split(":")[-1]
    element = root.xpath(f"//*[local-name()='element'][@name='{local}']")
    if not element:
        raise WfsError(f"DescribeFeatureType has no element {local!r}")
    type_ref = element[0].get("type", "").split(":")[-1]
    complex_types = root.xpath(f"//*[local-name()='complexType'][@name='{type_ref}']")
    if not complex_types:
        complex_types = element[0].xpath("*[local-name()='complexType']")
    if not complex_types:
        raise WfsError(f"No complexType found for {type_name!r}")
    attributes: dict[str, str] = {}
    geometry_property = None
    for el in complex_types[0].xpath(".//*[local-name()='sequence']/*[local-name()='element']"):
        name, xsd_type = el.get("name"), el.get("type", "")
        if not name:
            continue
        attributes[name] = xsd_type
        if xsd_type.startswith("gml:") and geometry_property is None:
            geometry_property = name
    return FeatureSchema(type_name, attributes, geometry_property)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass
class Page:
    start_index: int
    content: bytes
    number_returned: int


class WfsClient:
    def __init__(self, transport: Transport, version: str = "2.0.0"):
        self.transport = transport
        self.version = version

    def _base(self, request: str) -> dict[str, str]:
        return {"SERVICE": "WFS", "VERSION": self.version, "REQUEST": request}

    def capabilities(self) -> Capabilities:
        return parse_capabilities(self.transport.get(self._base("GetCapabilities")))

    def describe(self, type_name: str) -> FeatureSchema:
        params = self._base("DescribeFeatureType") | {"TYPENAMES": type_name}
        return parse_describe_feature_type(self.transport.get(params), type_name)

    def hits(self, type_name: str) -> int | None:
        params = self._base("GetFeature") | {"TYPENAMES": type_name, "RESULTTYPE": "hits"}
        root = _parse_xml(self.transport.get(params))
        matched = root.get("numberMatched")
        return int(matched) if matched and matched.isdigit() else None

    def pages(
        self,
        type_name: str,
        srs_name: str,
        *,
        page_size: int,
        max_features: int,
        paging: bool,
        sort_by: str | None = None,
        expected: int | None = None,
    ) -> Iterator[Page]:
        """Yield GetFeature pages until a short/empty page, ``expected`` is
        reached, or ``max_features`` would be exceeded (then raise)."""
        start = 0
        while True:
            params = self._base("GetFeature") | {
                "TYPENAMES": type_name,
                "SRSNAME": srs_name,
            }
            if paging:
                params |= {"STARTINDEX": str(start), "COUNT": str(page_size)}
            if sort_by:
                params["SORTBY"] = sort_by
            content = self.transport.get(params)
            root = _parse_xml(content)
            returned = root.get("numberReturned")
            n = int(returned) if returned and returned.isdigit() else len(
                root.xpath("*[local-name()='member' or local-name()='featureMember']"))
            yield Page(start, content, n)
            start += n
            if not paging or n == 0 or n < page_size:
                return
            if expected is not None and start >= expected:
                return
            if start >= max_features:
                raise WfsError(
                    f"Stopped at max_features={max_features}; raise the limit "
                    "deliberately if the layer is really this large"
                )
