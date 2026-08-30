from xscan.parsers import parse_dsd_event, parse_dsd_records, parse_fmp_line, parse_scanlist, validate_dsd_document, validate_scanlist


def test_fmp_parser_keeps_optional_values_out_of_label():
    item = parse_fmp_line("Tuning to 462.575 FM BW=12.500 DELAY=2 PL=114.8 GMRS CH16")
    assert item.frequency == "462.575"
    assert item.mode == "FM"
    assert item.options == {"BW": "12.500", "DELAY": "2", "PL": "114.8"}
    assert item.label == "GMRS CH16"


def test_dsd_event_parser_extracts_radio_metadata():
    item = parse_dsd_event("2026/08/21  14:23:45  Freq=155.000000  RAN=10  Group call; RID=10012 [CITY UNIT 12]    11s")
    assert item.frequency == "155.000000"
    assert item.ran_nac == "RAN=10"
    assert item.call_type == "Group call"
    assert item.radio_id == "10012"
    assert item.radio_alias == "CITY UNIT 12"
    assert item.decoder_duration == 11


def test_scanlist_parser_preserves_disabled_entries_and_warns_duplicates():
    text = "155.0000 NEXEDGE48 BW=6.25 DELAY=6 Dispatch\n;155.0000 FM BW=11.0 Backup\n155.0000 FM BW=11.0 Duplicate\n"
    items = parse_scanlist(text)
    assert [item.enabled for item in items] == [True, False, True]
    assert any("Duplicate" in issue["message"] for issue in validate_scanlist(text))


def test_dsd_records_keep_quoted_aliases_and_unknown_extra_fields():
    text = '; keep this comment\nNEXEDGE, 1, 2, 10012, 50, 0, 7, 2026/08/21, "City, Unit 12", future-field\n'
    records = parse_dsd_records("DSDPlus.radios", text)
    assert records[0]["fields"]["alias"] == "City, Unit 12"
    assert records[0]["fields"]["extra_10"] == "future-field"
    assert validate_dsd_document("DSDPlus.radios", text) == []


def test_dsd_event_parser_supports_nac_without_alias():
    item = parse_dsd_event("2026/08/21 14:23:45 Freq=155.000000 NAC=293 Private call; RID=42 3s")
    assert item.ran_nac == "NAC=293"
    assert item.call_type == "Private call"
    assert item.radio_id == "42"
    assert item.radio_alias == ""
