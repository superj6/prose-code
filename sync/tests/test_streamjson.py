from prosesync.streamjson import ArrayElementScanner


def test_yields_each_element_as_soon_as_complete():
    text = '{"edits": [{"op": "replace", "block": "b1", "text": "a }] {\\" b", "reason": "r"}, {"op": "delete", "block": "b2", "text": null, "reason": "x"}]}'
    sc = ArrayElementScanner()
    got = []
    for i in range(0, len(text), 7):  # arbitrary chunking
        got.extend(sc.feed(text[i : i + 7]))
    assert [g["block"] for g in got] == ["b1", "b2"]
    assert got[0]["text"] == 'a }] {" b'


def test_partial_text_while_streaming():
    from prosesync.streamjson import partial_text

    text = '{"edits": [{"op": "replace", "block": "b2", "text": "line 1\\nline \\"two\\" and \\\\ back'
    sc = ArrayElementScanner()
    list(sc.feed(text))
    block, so_far = partial_text(sc.current())
    assert block == "b2"
    assert so_far == 'line 1\nline "two" and \\ back'
    # dangling escape is tolerated
    list(sc.feed("\\"))
    assert partial_text(sc.current())[1] == 'line 1\nline "two" and \\ back'
    assert partial_text('{"op": "replace"') is None
