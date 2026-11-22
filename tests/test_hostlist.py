from nodetools_runtime import hostlist


def test_literal():
    assert hostlist.expand("node01") == ["node01"]


def test_numeric_range_padding():
    assert hostlist.expand("node[01-03]") == ["node01", "node02", "node03"]


def test_numeric_range_no_padding():
    assert hostlist.expand("node[1-3]") == ["node1", "node2", "node3"]


def test_list_and_range():
    assert hostlist.expand("gpu[001-003,007]") == [
        "gpu001",
        "gpu002",
        "gpu003",
        "gpu007",
    ]


def test_brace_range():
    assert hostlist.expand("x{8..10}") == ["x8", "x9", "x10"]


def test_multiple_tokens_and_dedup():
    got = hostlist.expand("n[1-2],n2,n3")
    assert got == ["n1", "n2", "n3"]


def test_real_world_pattern():
    got = hostlist.expand("vdc1-pro2-t10-gpu-cp-[01-03]")
    assert got == [
        "vdc1-pro2-t10-gpu-cp-01",
        "vdc1-pro2-t10-gpu-cp-02",
        "vdc1-pro2-t10-gpu-cp-03",
    ]
