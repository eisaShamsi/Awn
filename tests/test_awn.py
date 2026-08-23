"""Tests for the Awn assistant."""

from __future__ import annotations

import pytest

from awn import Awn
from awn.registry import SkillRegistry
from awn.skills import (
    CalculatorSkill,
    EchoSkill,
    HelpSkill,
    TextSkill,
    TimeSkill,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestSkillRegistry:
    def test_register_and_get(self):
        reg = SkillRegistry()
        skill = EchoSkill()
        reg.register(skill)
        assert reg.get("echo") is skill

    def test_get_unknown_returns_none(self):
        reg = SkillRegistry()
        assert reg.get("nonexistent") is None

    def test_all_skills_deduplicates(self):
        reg = SkillRegistry()
        skill = TimeSkill()
        reg.register(skill)
        skills = reg.all_skills()
        assert skills.count(skill) == 1


# ---------------------------------------------------------------------------
# EchoSkill
# ---------------------------------------------------------------------------


class TestEchoSkill:
    def setup_method(self):
        self.skill = EchoSkill()

    def test_echoes_text(self):
        assert self.skill.run("hello world") == "hello world"

    def test_empty_shows_usage(self):
        result = self.skill.run("")
        assert "Usage" in result


# ---------------------------------------------------------------------------
# TimeSkill
# ---------------------------------------------------------------------------


class TestTimeSkill:
    def setup_method(self):
        self.skill = TimeSkill()

    def test_returns_string(self):
        result = self.skill.run("")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_date_subcommand(self):
        result = self.skill.run("date")
        assert "Today is" in result

    def test_time_subcommand(self):
        result = self.skill.run("time")
        assert "current time" in result.lower()


# ---------------------------------------------------------------------------
# CalculatorSkill
# ---------------------------------------------------------------------------


class TestCalculatorSkill:
    def setup_method(self):
        self.skill = CalculatorSkill()

    def test_addition(self):
        assert self.skill.run("2 + 3") == "2 + 3 = 5"

    def test_multiplication(self):
        assert self.skill.run("6 * 7") == "6 * 7 = 42"

    def test_division(self):
        result = self.skill.run("10 / 4")
        assert "2.5" in result

    def test_integer_result_no_decimal(self):
        assert self.skill.run("10 / 2") == "10 / 2 = 5"

    def test_power(self):
        assert self.skill.run("2 ** 8") == "2 ** 8 = 256"

    def test_division_by_zero(self):
        result = self.skill.run("1 / 0")
        assert "zero" in result.lower()

    def test_invalid_expression(self):
        result = self.skill.run("import os")
        assert "Could not evaluate" in result

    def test_empty_shows_usage(self):
        result = self.skill.run("")
        assert "Usage" in result


# ---------------------------------------------------------------------------
# TextSkill
# ---------------------------------------------------------------------------


class TestTextSkill:
    def setup_method(self):
        self.skill = TextSkill()

    def test_upper(self):
        assert self.skill.run("upper hello") == "HELLO"

    def test_lower(self):
        assert self.skill.run("lower HELLO") == "hello"

    def test_title(self):
        assert self.skill.run("title hello world") == "Hello World"

    def test_reverse(self):
        assert self.skill.run("reverse abc") == "cba"

    def test_len(self):
        assert self.skill.run("len hello") == "5"

    def test_words(self):
        assert self.skill.run("words one two three") == "3"

    def test_unknown_subcommand(self):
        result = self.skill.run("blah text")
        assert "Unknown subcommand" in result

    def test_missing_text(self):
        result = self.skill.run("upper")
        assert "Usage" in result


# ---------------------------------------------------------------------------
# HelpSkill
# ---------------------------------------------------------------------------


class TestHelpSkill:
    def setup_method(self):
        self.reg = SkillRegistry()
        self.help = HelpSkill(self.reg)
        self.reg.register(self.help)
        self.reg.register(EchoSkill())

    def test_general_help_lists_commands(self):
        result = self.help.run("")
        assert "echo" in result
        assert "help" in result

    def test_specific_help(self):
        result = self.help.run("echo")
        assert "echo" in result
        assert "Usage" in result.lower() or "usage" in result.lower()

    def test_unknown_command_help(self):
        result = self.help.run("nonexistent")
        assert "No command" in result


# ---------------------------------------------------------------------------
# Awn (integration)
# ---------------------------------------------------------------------------


class TestAwn:
    def setup_method(self):
        self.awn = Awn()

    def test_empty_input_returns_empty(self):
        assert self.awn.handle("") == ""

    def test_whitespace_only_returns_empty(self):
        assert self.awn.handle("   ") == ""

    def test_echo_command(self):
        assert self.awn.handle("echo hi there") == "hi there"

    def test_calc_command(self):
        assert self.awn.handle("calc 1 + 1") == "1 + 1 = 2"

    def test_unknown_command(self):
        result = self.awn.handle("foobar")
        assert "don't know" in result.lower()

    def test_help_command(self):
        result = self.awn.handle("help")
        assert "echo" in result

    def test_greeting_contains_awn(self):
        assert "Awn" in self.awn.greeting

    def test_case_insensitive_command(self):
        assert self.awn.handle("ECHO hello") == "hello"
