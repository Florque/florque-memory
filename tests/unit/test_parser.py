import copy

import pytest
import yaml

from memory_engine.config import default_ontology_path
from memory_engine.core.errors import OntologyError, ValidationError
from memory_engine.core.parser import ParentRef, parse_ontology

RAW = yaml.safe_load(default_ontology_path().read_text())


def mutate(fn):
    data = copy.deepcopy(RAW)
    fn(data)
    return data


def assert_invalid(data, fragment):
    with pytest.raises(OntologyError) as info:
        parse_ontology(data)
    text = str(info.value)
    assert fragment in text, text


def test_workgraph_structure(ontology):
    assert ontology.identifier == "id"
    assert ontology.containment == "part_of"
    assert ontology.root == "Project"
    assert {c.name for c in ontology.concrete_classes()} == {
        "Project", "Goal", "WorkItem", "Document", "Signal", "Actor",
    }
    assert ontology.classes["Node"].abstract


def test_induced_slots_merge_ancestors_and_usage(ontology):
    work = ontology.classes["WorkItem"]
    assert list(work.slots)[:4] == ["id", "title", "summary", "status"]
    assert work.discriminator == "kind"
    assert work.slots["part_of"].required
    assert work.slots["part_of"].ranges == ("WorkItem", "Project")
    assert work.slots["serves"].multivalued and work.slots["serves"].is_edge
    assert ontology.classes["Document"].slots["part_of"].ranges == ("Project",)
    # slot_usage on one class must not leak into the shared slot
    assert ontology.classes["Actor"].slots["part_of"].ranges == ("Actor",)
    assert not ontology.classes["Actor"].slots["part_of"].required


def test_value_annotations(ontology):
    assert ontology.allowed_targets("WorkItem", "part_of", "task") == [
        ParentRef("WorkItem", "epic"), ParentRef("WorkItem", "initiative"),
    ]
    assert ontology.allowed_targets("WorkItem", "part_of", None) == [ParentRef("WorkItem"), ParentRef("Project")]
    assert ontology.required_edges("WorkItem", "initiative") == ["part_of", "serves"]
    assert ontology.required_edges("WorkItem", "task") == ["part_of"]


def test_edge_properties(ontology):
    member_of = ontology.classes["Actor"].slots["member_of"]
    assert member_of.edge_properties["role"].range == "Role"
    assert member_of.edge_properties["role"].required


def test_rules(ontology):
    [rule] = ontology.classes["Goal"].rules
    assert rule.when == {"level": "key_result"} and rule.require == ("target",)


def test_get_class_is_case_insensitive(ontology):
    assert ontology.get_class("workitem").name == "WorkItem"
    with pytest.raises(ValidationError, match="Known classes"):
        ontology.get_class("Ticket")
    with pytest.raises(ValidationError):
        ontology.get_class("Node")
    assert ontology.get_class("Node", concrete=False).abstract


def test_rejects_unsupported_keys():
    assert_invalid(mutate(lambda d: d["slots"]["title"].update(pattern="^x")), "unsupported key(s) pattern")
    assert_invalid(mutate(lambda d: d.update(types={})), "unsupported key(s) types")


def test_rejects_unsupported_import():
    assert_invalid(mutate(lambda d: d["imports"].append("other")), "Unsupported import")


def test_rejects_unknown_range():
    assert_invalid(mutate(lambda d: d["slots"]["due"].update(range="moment")), "unknown range `moment`")


def test_rejects_class_range_without_edge_annotation():
    assert_invalid(mutate(lambda d: d["slots"]["handle"].update(range="Actor")), "is not annotated `edge: true`")


def test_rejects_is_a_cycle():
    def cycle(d):
        d["classes"]["Node"]["is_a"] = "Project"
    assert_invalid(mutate(cycle), "is_a cycle")


def test_rejects_undefined_slot():
    assert_invalid(mutate(lambda d: d["classes"]["Goal"]["slots"].append("nope")), "undefined slot `nope`")


def test_rejects_bad_allowed_parent():
    def bad(d):
        d["enums"]["WorkKind"]["permissible_values"]["task"]["annotations"]["allowed_parent"] = "WorkItem:saga"
    assert_invalid(mutate(bad), "unknown value `WorkItem:saga`")

    def outside(d):
        d["enums"]["WorkKind"]["permissible_values"]["task"]["annotations"]["allowed_parent"] = "Goal"
    assert_invalid(mutate(outside), "outside the `part_of` range")


def test_rejects_circular_requirements():
    def loop(d):
        # tasks may only live under tasks: no task can ever be created
        d["enums"]["WorkKind"]["permissible_values"]["task"]["annotations"]["allowed_parent"] = "WorkItem:task"
    assert_invalid(mutate(loop), "`WorkItem:task` can never be created")


def test_rejects_edge_without_target_class():
    def untargeted(d):
        d["classes"]["Goal"]["slots"].append("about")
    assert_invalid(mutate(untargeted), "edge `about` has no target class")
