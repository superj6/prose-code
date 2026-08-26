from prosesync.streamjson import ArrayElementScanner


def test_yields_each_element_as_soon_as_complete():
    text = '{"edits": [{"op": "replace", "block": "b1", "text": "a }] {\\" b", "reason": "r"}, {"op": "delete", "block": "b2", "text": null, "reason": "x"}]}'
    sc = ArrayElementScanner()
    got = []
    for i in range(0, len(text), 7):  # arbitrary chunking
        got.extend(sc.feed(text[i : i + 7]))
    assert [g["block"] for g in got] == ["b1", "b2"]
    assert got[0]["text"] == 'a }] {" b'
