"""Laptop sync tool: the parts that don't need a browser."""

from sync.__main__ import page_payload
from sync.browser import PageRead, _course_prefix
from sync.platforms import detect, is_youtube_playlist
from sync.youtube import clock


def test_detects_the_users_sites():
    cases = {
        "https://seek.onlinedegree.iitm.ac.in/courses/ns_24t3_cs2008?id=4": "iitm",
        "https://www.youtube.com/playlist?list=PLgUwDviBIf0oF6QL8m22w1hIDC1vJ_BHz": "youtube",
        "https://takeuforward.org/strivers-a2z-dsa-course/": "striver",
        "https://www.coursera.org/learn/machine-learning/home/week/1": "coursera",
        "https://www.udemy.com/course/python/learn/lecture/1": "udemy",
        "https://leetcode.com/studyplan/top-interview-150/": "leetcode",
        "https://codeforces.com/problemset": "codeforces",
        "https://www.codechef.com/learn": "codechef",
        "https://www.geeksforgeeks.org/courses/dsa-self-paced": "gfg",
        "https://rankersgurukul.com/courses/102/content?activeTab=Content": "rankers",
        "https://www.parmaracademy.in/new-courses?folderId=3": "parmar",
        "https://testbook.com/ssc-cgl/test-series": "testbook",
        "https://learn.example.org/course/1": "learn",       # unknown site: generic
    }
    for url, name in cases.items():
        assert detect(url).name == name, url
    assert is_youtube_playlist("https://www.youtube.com/playlist?list=abc")
    assert not is_youtube_playlist("https://www.youtube.com/watch?v=abc")


def test_follow_stays_inside_the_course():
    assert _course_prefix("https://rankersgurukul.com/courses/102/content?x=1") == \
        "https://rankersgurukul.com/courses/102"
    assert _course_prefix("https://www.udemy.com/course/python/learn/lecture/1") == \
        "https://www.udemy.com/course/python"


def test_payload_refuses_login_pages_instead_of_sending_them():
    body, problem = page_payload([PageRead("u", "u/login", "Sign in", "", needs_login=True)], "iitm")
    assert body is None and "python -m sync login iitm" in problem


def test_payload_joins_pages():
    reads = [PageRead("u", "u", "Course", "Week 1\nL1 — 10:00"), PageRead("u2", "u2", "W2", "L2 — 5:00")]
    body, problem = page_payload(reads, "udemy")
    assert problem is None
    assert body["url"] == "u" and "=== PAGE: W2 (u2) ===" in body["text"]


def test_clock():
    assert clock(754) == "12:34" and clock(3723) == "1:02:03" and clock(None) is None
