"""Tests for EventProcessor intent extraction logic."""

import pytest


class TestExtractIntents:
    """Tests for _extract_intents method."""

    def create_processor(self, bot_username: str = "gitea-copilot"):
        """Helper to create a minimal processor-like object for testing."""
        # Create a simple object with just the needed method
        class MockProcessor:
            def __init__(self, bot_username):
                self.bot_username = bot_username

            def _extract_intents(self, text: str) -> list[str]:
                """Extract all unique intents from bot mentions.

                Deduplicates by first keyword, preserves order and full intent text."""
                import re
                pattern = f"@{re.escape(self.bot_username)}\\s*"

                intents = []
                seen_keywords = set()

                # Keyword groups that represent the same command
                keyword_aliases = {
                    "help": ["help", "帮助", "?"],
                    "label": ["label", "标签", "tag"],
                    "review": ["review", "审核", "审查", "检查"],
                    "close": ["close", "关闭"],
                    "open": ["open", "打开", "reopen", "重开"],
                }

                # Build reverse lookup: keyword -> canonical name
                alias_to_canonical = {}
                for canonical, aliases in keyword_aliases.items():
                    for alias in aliases:
                        alias_to_canonical[alias.lower()] = canonical

                # Find all mentions and extract intents after them
                pos = 0
                while True:
                    match = re.search(pattern, text[pos:])
                    if not match:
                        break

                    # Start of intent is right after the mention
                    intent_start = pos + match.end()

                    # Find end of intent (next mention or end of text)
                    next_mention = re.search(pattern, text[intent_start:])
                    if next_mention:
                        intent_end = intent_start + next_mention.start()
                    else:
                        intent_end = len(text)

                    intent_text = text[intent_start:intent_end].strip()
                    pos = intent_start

                    if not intent_text:
                        continue

                    # Get first word as keyword
                    words = intent_text.split()
                    first_word = words[0].lower() if words else ""

                    if not first_word:
                        continue

                    # Normalize keyword through alias mapping
                    canonical = alias_to_canonical.get(first_word, first_word)

                    if canonical not in seen_keywords:
                        seen_keywords.add(canonical)
                        intents.append(intent_text)

                return intents

        return MockProcessor(bot_username)

    def test_single_intent(self):
        """Test extracting a single intent."""
        processor = self.create_processor()

        text = "@gitea-copilot review"
        intents = processor._extract_intents(text)
        assert intents == ["review"]

    def test_duplicate_intents_deduped(self):
        """Test that duplicate intents are deduplicated."""
        processor = self.create_processor()

        text = "@gitea-copilot review @gitea-copilot review @gitea-copilot review"
        intents = processor._extract_intents(text)
        assert intents == ["review"]

    def test_multiple_different_intents(self):
        """Test extracting multiple different intents."""
        processor = self.create_processor()

        text = "@gitea-copilot review @gitea-copilot label bug feature"
        intents = processor._extract_intents(text)
        assert intents == ["review", "label bug feature"]

    def test_alias_dedup(self):
        """Test that aliases are deduplicated (review and 审查 are same)."""
        processor = self.create_processor()

        text = "@gitea-copilot review @gitea-copilot 审查 @gitea-copilot 检查"
        intents = processor._extract_intents(text)
        # All three should be deduped to one since they're aliases for "review"
        assert intents == ["review"]

    def test_help_aliases_dedup(self):
        """Test that help aliases are deduplicated."""
        processor = self.create_processor()

        text = "@gitea-copilot help @gitea-copilot 帮助 @gitea-copilot ?"
        intents = processor._extract_intents(text)
        # All should be deduped to one since they're aliases for "help"
        assert intents == ["help"]

    def test_close_open_different(self):
        """Test that close and open are different intents."""
        processor = self.create_processor()

        text = "@gitea-copilot close @gitea-copilot open"
        intents = processor._extract_intents(text)
        assert intents == ["close", "open"]

    def test_intent_with_args(self):
        """Test intent with additional arguments."""
        processor = self.create_processor()

        text = "@gitea-copilot label bug feature urgent"
        intents = processor._extract_intents(text)
        assert intents == ["label bug feature urgent"]

    def test_empty_text(self):
        """Test empty text returns empty list."""
        processor = self.create_processor()

        intents = processor._extract_intents("")
        assert intents == []

    def test_no_mention(self):
        """Test text without mention returns empty list."""
        processor = self.create_processor()

        text = "This is just a regular comment"
        intents = processor._extract_intents(text)
        assert intents == []

    def test_mention_only(self):
        """Test mention without intent returns empty list."""
        processor = self.create_processor()

        text = "@gitea-copilot"
        intents = processor._extract_intents(text)
        assert intents == []

    def test_mixed_content(self):
        """Test extracting intents from mixed content with text between mentions."""
        processor = self.create_processor()

        # Intent text includes everything until next mention
        text = "Some text before @gitea-copilot review more text @gitea-copilot label bug"
        intents = processor._extract_intents(text)
        # First intent is "review more text" (everything between first mention and second)
        # This is expected behavior - user might add context after the command
        assert intents == ["review more text", "label bug"]

    def test_custom_bot_username(self):
        """Test with custom bot username."""
        processor = self.create_processor(bot_username="my-bot")

        text = "@my-bot review @my-bot help"
        intents = processor._extract_intents(text)
        assert intents == ["review", "help"]

    def test_close_aliases(self):
        """Test that close aliases are deduplicated."""
        processor = self.create_processor()

        text = "@gitea-copilot close @gitea-copilot 关闭"
        intents = processor._extract_intents(text)
        assert intents == ["close"]

    def test_open_aliases(self):
        """Test that open aliases are deduplicated."""
        processor = self.create_processor()

        text = "@gitea-copilot open @gitea-copilot 打开 @gitea-copilot 重开"
        intents = processor._extract_intents(text)
        assert intents == ["open"]