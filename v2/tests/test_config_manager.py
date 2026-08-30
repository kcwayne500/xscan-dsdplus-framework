import pytest

from xscan.config_manager import ConfigManager, RevisionConflict


def test_scanlist_patch_preserves_comments_and_creates_backup(app_paths):
    source = app_paths.dsdplus / "FMP24.ScanList"
    original = "; heading\n\n155.0000  NEXEDGE48 BW=6.25 DELAY=6  Dispatch\n"
    source.write_text(original, encoding="utf-8")
    manager = ConfigManager(app_paths)
    opened = manager.read("scanlist")
    saved = manager.patch_lines(
        "scanlist",
        [{"line_number": 3, "enabled": False, "frequency": "155.0000", "mode": "NEXEDGE48", "options": ["BW=6.25", "DELAY=6"], "label": "Dispatch"}],
        opened["revision"],
    )
    assert source.read_text(encoding="utf-8").startswith("; heading\n\n;")
    assert saved["backup"]
    assert list((app_paths.backups / "FMP24.ScanList").glob("*.bak"))


def test_revision_conflict_rejects_overwrite(app_paths):
    source = app_paths.dsdplus / "DSDPlus.networks"
    source.write_text('; test\nP25, 1, "One"\n', encoding="utf-8")
    manager = ConfigManager(app_paths)
    opened = manager.read("networks")
    source.write_text('; changed externally\nP25, 1, "One"\n', encoding="utf-8")
    with pytest.raises(RevisionConflict):
        manager.save_text("networks", opened["text"], opened["revision"])


def test_scanlist_can_append_a_channel_without_rewriting_existing_lines(app_paths):
    source = app_paths.dsdplus / "FMP24.ScanList"
    source.write_text("; heading\n155.0000 FM DELAY=2 Dispatch\n", encoding="utf-8")
    manager = ConfigManager(app_paths)
    opened = manager.read("scanlist")
    manager.patch_lines(
        "scanlist",
        [{"line_number": 3, "enabled": True, "frequency": "155.5000", "mode": "FM", "options": ["DELAY=6"], "label": "Channel Two"}],
        opened["revision"],
    )
    saved = source.read_text(encoding="utf-8")
    assert saved.startswith("; heading\n155.0000 FM DELAY=2 Dispatch\n")
    assert saved.endswith("155.5000  FM  DELAY=6  Channel Two\n")


def test_structured_dsd_patch_preserves_comments_order_and_extra_fields(app_paths):
    source = app_paths.dsdplus / "DSDPlus.radios"
    original = '; heading\n\nNEXEDGE, 1, 2, 10012, 50, 0, 7, 2026/08/21, "Old alias", future\n; footer\n'
    source.write_text(original, encoding="utf-8")
    manager = ConfigManager(app_paths)
    opened = manager.read("radios")
    fields = dict(opened["records"][0]["fields"])
    fields["alias"] = "New, alias"
    manager.patch_lines("radios", [{"line_number": 3, "fields": fields}], opened["revision"])
    saved = source.read_text(encoding="utf-8")
    assert saved.startswith("; heading\n\n")
    assert saved.endswith("\n; footer\n")
    assert '"New, alias",future' in saved
