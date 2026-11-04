from __future__ import annotations

from manimagent.budget import Budget
from manimagent.config import Settings
from manimagent.materials.service import MaterialService
from manimagent.schemas import InputsSource, ParseRequest, Usage
from tests.support import store_for


def test_generation_source_reuses_parse_contract_without_expanding_parser_audit(tmp_path):
    settings = Settings(private_dir=tmp_path)
    source = InputsSource(
        type="inputs", inputs=[{"type": "text", "text": "17+25"}], instruction="求和"
    )
    record = {}
    result = MaterialService(store_for(settings), settings).parse(
        source, Budget(settings, Usage()), record
    )
    assert result.blocks[0].content == "17+25"
    assert set(record["parse"]["request"]) == set(ParseRequest.model_fields)
