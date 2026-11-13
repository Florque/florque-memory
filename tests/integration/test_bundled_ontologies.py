"""Every ontology in ontologies/ must load, build a skill and accept its own examples on real
Ladybug. Adding a file to ontologies/ is enough to put it under these tests."""

from datetime import date

import pytest

from memory_engine.config import bundled_ontologies
from memory_engine.core.engine import OntologyEngine
from memory_engine.core.errors import ValidationError
from memory_engine.core.parser import load_ontology
from memory_engine.core.schema import schema_for
from memory_engine.core.skill import build_skill, example_payloads, skill_name
from memory_engine.database.ladybug_driver import LadybugDriver

pytestmark = pytest.mark.ladybug


def ladybug_engine(ontology):
    driver = LadybugDriver(":memory:", ontology.identifier)
    driver.ensure_schema(schema_for(ontology))
    return OntologyEngine(ontology, driver, actor="agent-test")


@pytest.mark.parametrize("name", sorted(bundled_ontologies()))
def test_bundled_ontology(name, tmp_path):
    path = bundled_ontologies()[name]
    ontology = load_ontology(path)
    engine = ladybug_engine(ontology)
    try:
        examples = example_payloads(ontology)
        assert {cls for cls, _ in examples} == {c.name for c in ontology.concrete_classes()}
        for cls, payload in examples:
            engine.remember(cls, payload)
        assert engine.validate()["valid"]
    finally:
        engine.db.close()
    out = build_skill(path, tmp_path / skill_name(ontology))
    assert (out / "SKILL.md").read_text().startswith(f"---\nname: {skill_name(ontology)}\n")


# ------------------------------------------------------------ family_tree scenario


@pytest.fixture
def family():
    ontology = load_ontology(bundled_ontologies()["family_tree"])
    engine = ladybug_engine(ontology)
    yield engine
    engine.db.close()


def seed_family(engine):
    r = engine.remember
    r("FamilyTree", {"id": "tree", "title": "Sidorov line", "summary": "Sidorovs of Tver governorate, 1850-1950."})
    r("Place", {"id": "tver", "title": "Tver Governorate", "kind": "region", "part_of": "tree",
                "modern_name": "Tver Oblast, Russia"})
    r("Place", {"id": "ostashkov", "title": "Ostashkov", "kind": "settlement", "part_of": "tver",
                "latitude": 57.15, "longitude": 33.1})
    r("Source", {"id": "metric-1890", "title": "Metric book, Ostashkov cathedral, 1890", "kind": "church_record",
                 "repository": "State Archive of Tver Oblast, fond 160, opis 1, delo 12345", "part_of": "tree"})
    r("Person", {"id": "petr", "title": "Petr Ivanovich Sidorov", "given_names": "Petr Ivanovich",
                 "surname": "Sidorov", "sex": "male", "part_of": "tree"})
    r("Person", {"id": "anna", "title": "Anna Vasilyevna Sidorova", "surname": "Kuznetsova",
                 "other_names": ["Sidorova", "Kusnezowa"], "sex": "female", "part_of": "tree"})
    r("Family", {"id": "fam-petr-anna", "title": "Sidorov – Kuznetsova", "kind": "marriage", "part_of": "tree"})
    for partner in ("petr", "anna"):
        engine.link(partner, "partner_in", "fam-petr-anna")
    r("Person", {"id": "ivan", "title": "Ivan Petrovich Sidorov", "sex": "male", "part_of": "tree",
                 "child_of": [{"id": "fam-petr-anna", "pedigree": "birth"}],
                 "cites": [{"id": "metric-1890", "page": "f. 15v, entry 42", "quality": "primary"}]})
    r("Event", {"id": "ivan-birth", "title": "Birth of Ivan Sidorov", "kind": "birth", "part_of": "tree",
                "date": "1890-03-24", "date_text": "12 MAR 1890 (Julian)",
                "about": [{"id": "ivan", "role": "principal"}, {"id": "petr", "role": "parent"}],
                "occurred_at": "ostashkov",
                "cites": [{"id": "metric-1890", "page": "f. 15v, entry 42", "quality": "primary"}]})


def test_family_tree_scenario(family):
    seed_family(family)
    assert family.validate()["valid"]

    ivan = family.recall("Person", {"id": "ivan"})[0]
    assert ivan["status"] == "proposed"  # nothing is fact until confirmed
    assert family.remember("Person", {"id": "ivan", "status": "confirmed"})["node"]["status"] == "confirmed"

    birth = family.recall("Event", {"kind": "birth"})[0]
    assert birth["date"] == date(1890, 3, 24) and birth["date_text"] == "12 MAR 1890 (Julian)"

    # a spelling variant from a German record finds the person
    assert [n["id"] for n in family.search("kusnezowa")] == ["anna"]

    # children of a family, and a child's parents through the family
    assert [n["id"] for n in family.recall("Person", {"child_of": "fam-petr-anna"})] == ["ivan"]
    assert {n["id"] for n in family.recall("Person", {"partner_in": "fam-petr-anna"})} == {"petr", "anna"}

    ctx = family.context("ivan")
    links = {(link["direction"], link["slot"], link["node"]["id"]): link for link in ctx["links"]}
    assert links[("out", "child_of", "fam-petr-anna")]["edge"] == {"pedigree": "birth"}
    assert links[("out", "cites", "metric-1890")]["edge"] == {"page": "f. 15v, entry 42", "quality": "primary"}
    assert links[("in", "about", "ivan-birth")]["edge"] == {"role": "principal"}

    # place hierarchy is the containment tree: event -> settlement -> region -> tree
    place_ctx = family.context("ostashkov")
    assert [a["id"] for a in place_ctx["ancestors"]] == ["tver", "tree"]


def test_family_tree_rejections(family):
    seed_family(family)
    with pytest.raises(ValidationError, match="`about` is required"):
        family.remember("Event", {"title": "Orphan event", "kind": "death", "part_of": "tree"})
    with pytest.raises(ValidationError, match="not a valid CitationQuality"):
        family.link("ivan", "cites", "metric-1890", {"quality": "certain"})
    with pytest.raises(ValidationError, match="cannot point to"):
        family.remember("Event", {"title": "Bad", "kind": "death", "part_of": "tree", "about": ["metric-1890"]})
    with pytest.raises(ValidationError, match="containment cycle"):
        family.link("tver", "part_of", "ostashkov")
