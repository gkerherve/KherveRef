from kherveref import fetch
from kherveref.add_dialog import AddByIdentifierDialog, find_identifiers


def test_find_identifiers_in_mixed_text():
    text = """10.1038/nature14539
https://arxiv.org/abs/1706.03762v5
ISBN 978-0-262-03561-3
Smith J. (2020) Surfaces. Appl. Surf. Sci. 512, 145. doi:10.1016/j.apsusc.2020.145000.
just some words
10.1038/nature14539"""
    found, unknown = find_identifiers(text)
    assert found == [("doi", "10.1038/nature14539"), ("arxiv", "1706.03762"),
                     ("isbn", "9780262035613"),
                     ("doi", "10.1016/j.apsusc.2020.145000")]
    assert unknown == ["just some words"]


def test_requests_round_trip_through_classify():
    found, _ = find_identifiers("1706.03762\n0-306-40615-2")
    dlg_requests = [("arxiv", "1706.03762"), ("isbn", "0306406152")]
    assert found == dlg_requests
    from kherveref.add_dialog import as_request
    assert [fetch.classify(as_request(*f)) for f in found] == dlg_requests


def test_dialog_button_reflects_what_was_found(qapp):
    dlg = AddByIdentifierDialog(text="")
    ok = dlg._buttons.button(dlg._buttons.StandardButton.Ok)
    assert not ok.isEnabled()
    dlg._edit.setPlainText("10.1038/nature14539\nnonsense\narXiv:2101.00001")
    assert ok.isEnabled() and ok.text() == "Add 2 references"
    assert dlg._found.count() == 3
    assert dlg.requests() == ["10.1038/nature14539", "arXiv:2101.00001"]
