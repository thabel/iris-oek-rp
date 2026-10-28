import pytest
import requests

from oek_harvester.wfs import (
    FixtureTransport,
    HttpTransport,
    WfsClient,
    WfsError,
    parse_capabilities,
    raise_for_ows_exception,
)


def test_capabilities(fixture_dir):
    caps = parse_capabilities((fixture_dir / "capabilities.xml").read_bytes())
    assert caps.implements_paging is True
    assert caps.count_default == 5000
    assert caps.provider.startswith("Struktur- und Genehmigungsdirektion Nord")
    ft = caps.find_feature_type("oek_f")  # local name lookup
    assert ft.name == "ms:oek_f"
    assert ft.default_crs == "urn:ogc:def:crs:EPSG::31466"
    assert "urn:ogc:def:crs:EPSG::4326" in ft.other_crs
    assert caps.find_feature_type("rp_oekokonto") is None  # name from the brief: not advertised


def test_describe_feature_type(fixture_dir):
    schema = WfsClient(FixtureTransport(fixture_dir)).describe("ms:oek_f")
    assert schema.geometry_property == "geom"
    assert schema.attributes["oek_id"] == "string"


def test_paging_stops_on_short_page(fixture_dir):
    transport = FixtureTransport(fixture_dir)
    client = WfsClient(transport)
    pages = list(client.pages("ms:oek_f", "urn:ogc:def:crs:EPSG::31466", page_size=5,
                              max_features=1000, paging=True, expected=13))
    assert [p.start_index for p in pages] == [0, 5, 10]
    assert [p.number_returned for p in pages] == [5, 5, 3]
    assert all("COUNT=5" in r.url for r in transport.requests_made)


def test_max_features_guard(fixture_dir):
    client = WfsClient(FixtureTransport(fixture_dir))
    with pytest.raises(WfsError, match="max_features"):
        list(client.pages("ms:oek_f", "x", page_size=5, max_features=5, paging=True))


def test_ows_exception_is_raised():
    report = (b'<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1">'
              b'<ows:Exception exceptionCode="InvalidParameterValue">'
              b'<ows:ExceptionText>Unknown typename</ows:ExceptionText>'
              b'</ows:Exception></ows:ExceptionReport>')
    with pytest.raises(WfsError, match="Unknown typename"):
        raise_for_ows_exception(report)


class FakeResponse:
    def __init__(self, status, content=b"<ok/>", headers=None):
        self.status_code, self.content = status, content
        self.headers = headers or {}
        self.url = "https://example.test/wfs"


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def transport(session, retries=2):
    return HttpTransport("https://example.test/wfs", timeout_s=1, max_retries=retries,
                         backoff_s=0, request_delay_s=0, user_agent="test", session=session)


def test_http_retries_on_503_then_succeeds():
    session = FakeSession([FakeResponse(503), FakeResponse(200, b"<WFS_Capabilities/>")])
    assert transport(session).get({"REQUEST": "GetCapabilities"}) == b"<WFS_Capabilities/>"
    assert session.calls == 2


def test_http_retry_after_header_is_honoured(monkeypatch):
    slept = []
    monkeypatch.setattr("oek_harvester.wfs.time.sleep", lambda s: slept.append(s))
    session = FakeSession([FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200)])
    transport(session).get({})
    assert 7.0 in slept


@pytest.mark.parametrize("status", [401, 403, 404])
def test_http_client_errors_are_not_retried(status):
    session = FakeSession([FakeResponse(status)])
    with pytest.raises(WfsError, match=str(status)):
        transport(session).get({})
    assert session.calls == 1


def test_http_gives_up_after_retries():
    session = FakeSession([requests.ConnectionError("dns")] * 3)
    t = transport(session, retries=2)
    with pytest.raises(WfsError, match="Request failed"):
        t.get({})
    assert session.calls == 3
    assert all(r.error for r in t.requests_made)
