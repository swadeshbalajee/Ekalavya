import pytest

from scholarmotion.retrieval.paper_download import _validate_public_https_url
from scholarmotion.retrieval.paper_ingestion import PaperParser


@pytest.mark.parametrize(
    "url",
    [
        "http://arxiv.org/pdf/1234.5678.pdf",
        "https://127.0.0.1/paper.pdf",
        "https://10.0.0.2/paper.pdf",
        "https://localhost/paper.pdf",
        "https://user:pass@example.org/paper.pdf",
    ],
)
def test_paper_link_rejects_non_https_or_private_destinations(url):
    with pytest.raises(ValueError):
        _validate_public_https_url(url)


def test_paper_header_skips_arxiv_reuse_notice_and_extracts_title():
    title, authors = PaperParser._header_fields(
        [
            "Provided proper attribution is provided, Google hereby grants permission to",
            "reproduce the tables and figures in this paper solely for use in journalistic or",
            "scholarly works.",
            "Attention Is All You Need",
            "Ashish Vaswani∗",
            "Google Brain",
            "avaswani@google.com",
        ],
        None,
        "1706.03762",
    )
    assert title == "Attention Is All You Need"
    assert authors == ["Ashish Vaswani"]
