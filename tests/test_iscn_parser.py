"""Tests for the educational ISCN karyotype subset parser (task P4-001).

All karyotypes below are synthetic examples of notation, not patient data.
"""

from __future__ import annotations

import pytest

from src.reports import (
    ISCN_SUBSET_NOTE,
    MAX_ISCN_CLONES,
    MAX_ISCN_LENGTH,
    ISCNClone,
    parse_iscn,
)


def _kinds(result):
    return [issue.kind for issue in result.issues]


# --- accepted subset --------------------------------------------------------


def test_normal_karyotype_is_accepted():
    result = parse_iscn("46,XX")
    assert result.is_valid
    assert result.clones == [ISCNClone(46, "XX", (), None)]
    assert result.abnormalities == []
    assert result.issues == []
    assert result.notes == ISCN_SUBSET_NOTE


def test_translocation_with_cell_count_is_structured():
    result = parse_iscn("46,XY,t(9;22)(q34;q11.2)[20]")
    assert result.is_valid
    assert result.clones == [ISCNClone(46, "XY", ("t(9;22)(q34;q11.2)",), 20)]
    assert result.abnormalities == ["t(9;22)(q34;q11.2)"]


def test_numerical_gain_and_loss_are_accepted():
    assert parse_iscn("47,XX,+8[15]").clones == [ISCNClone(47, "XX", ("+8",), 15)]
    assert parse_iscn("45,XY,-7[20]").clones == [ISCNClone(45, "XY", ("-7",), 20)]
    assert parse_iscn("48,XY,+8,+21[10]").clones == [ISCNClone(48, "XY", ("+8", "+21"), 10)]


def test_multiple_clones_keep_order_and_counts():
    result = parse_iscn("47,XX,+8[15]/46,XX[5]")
    assert result.is_valid
    assert result.clones == [
        ISCNClone(47, "XX", ("+8",), 15),
        ISCNClone(46, "XX", (), 5),
    ]
    assert result.abnormalities == ["+8"]


def test_mixed_numerical_and_translocation_in_chromosome_order():
    result = parse_iscn("47,XY,+8,t(9;22)(q34;q11.2)[20]")
    assert result.is_valid
    assert result.abnormalities == ["+8", "t(9;22)(q34;q11.2)"]


def test_leading_and_trailing_whitespace_is_trimmed_but_raw_is_preserved():
    text = "  46,XX,t(8;21)(q22;q22.1)[20]\n"
    result = parse_iscn(text)
    assert result.is_valid
    assert result.raw == text


# --- malformed supported notation --------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "46",
        "46,",
        "4x,XX",
        "046,XX",
        "46,XZ",
        "46,XX,",
        "46,XX,,+8",
        "46,XX/",
        "/46,XX",
        "47,XX,+23",
        "47,XX,+0",
        "46,XX,t(9;22)",
        "46,XX,t(9;22)(q34)",
        "46,XX,t(9;22)(q34;r11)",
        "46,XX,t(9;22)(q34;q11.2.3)",
        "46,XY,t(22;9)(q11.2;q34)",
        "46,XX,t(9;9)(p13;q34)",
        "46,XX,t(9;23)(q34;q11)",
        "46,XX[0]",
        "46,XX[]",
        "46,XX[x]",
        "46,XX[20",
        "46,XX[20]x",
        "46,XX[20][5]",
        "46, XX",
        "47,XY,+21,+8",
        "46,XX,+8",
        "47,XX[20]",
        "46,XX,-7[20]",
        "46,XX,tt(9;22)(q34;q11.2)",
    ],
)
def test_malformed_supported_notation_is_rejected(text):
    result = parse_iscn(text)
    assert not result.is_valid
    assert result.issues, text
    assert "malformed" in _kinds(result)
    assert "unsupported" not in _kinds(result)
    assert result.clones == []
    assert result.abnormalities == []
    assert result.notes.startswith("Rejected (malformed)")


def test_count_mismatch_names_the_expected_total():
    result = parse_iscn("46,XX,+8[20]")
    assert not result.is_valid
    assert "46 + 1 gain(s) - 0 loss(es) = 47" in result.issues[0].message


# --- unsupported ISCN constructs ---------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "46,XX,del(5)(q13q33)[10]",
        "46,XY,inv(16)(p13.1q22)[20]",
        "46,XX,i(17)(q10)[10]",
        "46,XY,dup(1)(q21q32)",
        "46,XX,add(7)(q22)",
        "46,XY,der(1;7)(q10;p10)",
        "47,XXY",
        "45,X",
        "47,XY,+X",
        "45,X,-X",
        "47,XX,+mar[5]",
        "47,XX,+21c",
        "46,XX,t(X;1)(p11.2;q21)",
        "46,XX,t(1;3;5)(p32;q21;q31)",
        "45~48,XX,+8",
        "46,XX,?t(9;22)(q34;q11.2)",
        "46,XX[cp10]",
        "46,XX//46,XY",
        "47,XX,+8[10]/48,idem,+21[5]",
    ],
)
def test_unsupported_constructs_are_rejected_as_unsupported(text):
    result = parse_iscn(text)
    assert not result.is_valid
    assert "unsupported" in _kinds(result), [(i.kind, i.token) for i in result.issues]
    assert result.clones == []
    assert result.abnormalities == []


def test_mixed_supported_and_unsupported_input_is_never_partially_valid():
    result = parse_iscn("47,XX,+8,del(5)(q13q33)[20]")
    assert not result.is_valid
    assert result.clones == []
    assert result.abnormalities == []
    assert [(i.kind, i.token) for i in result.issues] == [("unsupported", "del(5)(q13q33)")]


def test_every_problem_in_every_clone_is_reported():
    result = parse_iscn("47,XY,+21,+8[10]/46,XX,inv(16)(p13.1q22)[5]")
    assert not result.is_valid
    assert [(i.kind, i.token) for i in result.issues] == [
        ("malformed", "+8"),
        ("unsupported", "inv(16)(p13.1q22)"),
    ]


# --- bounds and types ---------------------------------------------------------


def test_input_length_and_clone_count_are_bounded():
    long_text = "46,XX" + ",+8" * MAX_ISCN_LENGTH
    assert not parse_iscn(long_text).is_valid
    many_clones = "/".join(["46,XX[1]"] * (MAX_ISCN_CLONES + 1))
    result = parse_iscn(many_clones)
    assert not result.is_valid
    assert f"More than {MAX_ISCN_CLONES} clones." in result.issues[0].message
    assert parse_iscn("/".join(["46,XX[1]"] * MAX_ISCN_CLONES)).is_valid


def test_non_string_input_raises_type_error():
    with pytest.raises(TypeError):
        parse_iscn(None)  # type: ignore[arg-type]
