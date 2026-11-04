"""Synthetic offline pages exercise discovery parsing; no real search is claimed."""

from html import escape
from urllib.parse import quote

import pytest
from pydantic import ValidationError

from steal_her_heart.search_html import SearchHit, SearchPageError, parse_duckduckgo_html


def result(url, title="Example product", snippet="A public search summary.", classes="result"):
    return (
        f'<div class="{classes}"><div class="result__body"><h2 class="result__title">'
        f'<a class="result__a" href="{escape(url, quote=True)}">{title}</a></h2>'
        f'<a class="result__snippet" href="{escape(url, quote=True)}">{snippet}</a></div></div>'
    )


def parse(text, limit=5):
    return parse_duckduckgo_html(text.encode(), limit)


def test_nested_multilingual_titles_and_snippets_stay_with_their_result():
    page = '<a href="https://navigation.example.com/">Navigation</a>'
    page += result(
        "https://example.com/a", "工具 <b>A</b> &amp; 服务", "中文 <em>说明</em> &lt;真实片段&gt;"
    )
    page += result("https://example.org/b", "Second", "Different summary")
    hits = parse(page)
    assert len(hits) == 2
    assert hits[0].title == "工具 A & 服务"
    assert hits[0].snippet == "中文 说明 <真实片段>"
    assert hits[1].title == "Second" and hits[1].snippet == "Different summary"
    assert [hit.rank for hit in hits] == [1, 2]


def test_redirects_are_unwrapped_once_and_urls_deduplicated_before_limit():
    target = "https://example.com/a?x=1&y=2"
    redirect = "//duckduckgo.com/l/?uddg=" + quote(target, safe="") + "&rut=ignored"
    page = result(redirect) + result(target + "#section") + result("https://example.org/")
    hits = parse(page, 2)
    assert len(hits) == 2 and str(hits[0].url) == target
    assert [hit.rank for hit in hits] == [1, 2]
    assert parse(result(target))[0].id == hits[0].id


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,hello",
        "file:///etc/passwd",
        "ftp://example.com/a",
        "http://user:password@example.com/",
        "http://example.com:8080/",
        "http://localhost/",
        "http://localhost.localdomain/",
        "http://printer/",
        "https://service.internal/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.10/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[fd00::1]/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://127.1/",
        "http://[::ffff:127.0.0.1]/",
        "http://[64:ff9b::a9fe:a9fe]/",
        "//duckduckgo.com/l/?uddg=javascript%3Aalert(1)",
        "//duckduckgo.com/l/?uddg=http%3A%2F%2F10.0.0.1",
        "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com&uddg=https%3A%2F%2Fexample.org",
        "//duckduckgo.com/l/?uddg=https%253A%252F%252Fexample.com",
        "https://html.duckduckgo.com/html/?q=other",
        "https://duckduckgo.com/y.js?ad=1",
    ],
)
def test_unsafe_or_unusable_links_are_not_returned_as_candidates(url):
    hits = parse(result(url) + result("https://example.com/valid"))
    assert len(hits) == 1 and str(hits[0].url) == "https://example.com/valid"
    assert hits[0].rank == 1


def test_actual_duckduckgo_help_pages_can_be_results_not_just_navigation():
    url = "https://duckduckgo.com/duckduckgo-help-pages/features/non-javascript"
    assert str(parse(result(url))[0].url) == url


@pytest.mark.parametrize(
    "challenge",
    [
        '<form id="challenge-form"></form>',
        '<form action="/anomaly.js?token=private"></form>',
        '<div class="anomaly-modal__mask"></div>',
        '<script src="/anomaly.js"></script>',
        '<div class="g-recaptcha"></div>',
    ],
)
def test_structural_challenge_is_a_restriction_even_if_page_contains_results(challenge):
    with pytest.raises(SearchPageError) as caught:
        parse(result("https://example.com/") + challenge)
    assert caught.value.code == "SEARCH_ACCESS_RESTRICTED"
    assert "private" not in caught.value.message


def test_a_normal_search_result_about_captcha_is_not_misclassified():
    hits = parse(
        result(
            "https://example.com/captcha", "CAPTCHA documentation", "How to verify you are human"
        )
    )
    assert hits[0].title == "CAPTCHA documentation"


def test_only_explicit_no_results_markers_produce_an_empty_success():
    assert parse('<div class="no-results"><h1>No results found</h1></div>') == ()
    with pytest.raises(SearchPageError) as caught:
        parse("<html><h1>New search layout</h1></html>")
    assert caught.value.code == "SEARCH_FORMAT_ERROR"
    with pytest.raises(SearchPageError):
        parse('<div class="no-results"></div>' + result("https://example.com/"))


def test_malformed_and_all_filtered_results_are_not_false_no_results():
    for page in [
        result("http://127.0.0.1/"),
        '<div class="result"><a class="result__a">Missing URL</a></div>',
        '<div class="result"><a class="result__a" href="https://example.com/">Unclosed</div>',
    ]:
        with pytest.raises(SearchPageError) as caught:
            parse(page)
        assert caught.value.code == "SEARCH_NO_USABLE_RESULTS"


def test_script_style_template_and_ad_contents_are_not_in_hits():
    page = "<script>" + result("https://evil.example.com/") + "</script>"
    page += (
        "<style>ignored style</style><template>"
        + result("https://template.example.com/")
        + "</template>"
    )
    page += result("https://ad.example.com/", classes="result result--ad")
    page += result(
        "https://example.com/",
        "Title<script>wrong</script>",
        "Text<style>wrong</style><b> only</b>",
    )
    hits = parse(page)
    assert len(hits) == 1 and hits[0].title == "Title" and hits[0].snippet == "Text only"


def test_a_snippet_outside_a_result_is_not_attached_to_a_different_anchor():
    page = '<a class="result__a" href="https://example.com/">Title</a>'
    page += '<a class="result__snippet">Unrelated</a>'
    assert parse(page)[0].snippet == ""


def test_duplicate_attributes_do_not_rewrite_the_actual_link_or_challenge_marker():
    page = '<a class="result__a" href="http://127.0.0.1/" href="https://example.com/">Unsafe</a>'
    page += result("https://example.org/", "Valid")
    assert [str(hit.url) for hit in parse(page)] == ["https://example.org/"]
    with pytest.raises(SearchPageError) as caught:
        parse(result("https://example.com/") + '<form id="challenge-form" id="other"></form>')
    assert caught.value.code == "SEARCH_ACCESS_RESTRICTED"


@pytest.mark.parametrize("limit", [0, 11, True, 1.0, "5"])
def test_result_limits_are_strict_and_bounded(limit):
    with pytest.raises(ValueError):
        parse(result("https://example.com/"), limit)


def test_html_and_text_limits_are_bounded():
    with pytest.raises(SearchPageError):
        parse_duckduckgo_html(b"x" * 1048577, 5)
    with pytest.raises(SearchPageError):
        parse("<div>" * 1025)
    with pytest.raises(SearchPageError):
        parse("")
    hit = parse(result("https://example.com/", "a" * 5000, "b" * 5000))[0]
    assert len(hit.title) == len(hit.snippet) == 4000


def test_hit_contract_rejects_extra_fields_and_noninteger_rank():
    hit = parse(result("https://example.com/"))[0]
    with pytest.raises(ValidationError):
        SearchHit(**hit.model_dump(), fact="This is not Evidence")
    values = hit.model_dump()
    values["rank"] = True
    with pytest.raises(ValidationError):
        SearchHit.model_validate(values)
