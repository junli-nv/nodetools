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


def test_split_top_ignores_bracketed_commas():
    assert hostlist.split_top("n[01,03-05],n07") == ["n[01,03-05]", "n07"]


def test_compact_single():
    assert hostlist.compact(["nfs01"]) == "nfs01"


def test_compact_gap_range():
    assert hostlist.compact(["n01", "n02", "n10"]) == "n[01-02,10]"


def test_compact_multi_field():
    names = [
        "vdc1-pro2-{}".format(name)
        for name in (
            "n03-gpu-cp-01",
            "n03-gpu-cp-02",
            "n04-gpu-cp-01",
            "n04-gpu-cp-02",
        )
    ]
    assert hostlist.compact(names) == "vdc1-pro2-n[03-04]-gpu-cp-[01-02]"


def test_compact_dedup_and_width():
    assert hostlist.compact(["n1", "n1", "n2"]) == "n[1-2]"


def test_compact_expand_roundtrip():
    names = [
        "vdc1-pro2-{}{:02d}-gpu-cp-{:02d}".format(rack, rack_no, i)
        for rack, rack_no in (("n", 3), ("o", 7))
        for i in range(1, 19)
    ] + ["nfs01"]
    comp = hostlist.compact(names)
    assert set(hostlist.expand(comp)) == set(names)
