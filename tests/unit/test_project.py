import pytest

from memory_engine.core.errors import OntologyError
from memory_engine.core.parser import parse_ontology
from memory_engine.core.project import merge_ontologies
from memory_engine.core.schema import schema_for


def _work():
    return parse_ontology({
        "name": "work",
        "id": "https://x/work",
        "default_range": "string",
        "slots": {
            "id": {"identifier": True},
            "name": {"required": True},
            "part_of": {"range": "Epic", "annotations": {"edge": True}},
        },
        "classes": {
            "Epic": {"slots": ["id", "name", "part_of"]},
            "Finding": {"slots": ["id", "name"]},
            "Task": {"slots": ["id", "name", "part_of"], "attributes": {
                "addresses": {"annotations": {"edge": True, "target_role": "finding"}, "multivalued": True},
                "touches": {"annotations": {"edge": True, "target_role": "component"}, "multivalued": True},
            }},
        },
    })


def _domain(**extra):
    classes = {
        "Component": {"slots": ["id", "name"], "annotations": {"bridge_role": "component"}},
        "Bug": {"slots": ["id", "name"], "annotations": {"bridge_role": "finding, component"}},
    }
    classes.update(extra)
    return parse_ontology({
        "name": "domain",
        "id": "https://x/domain",
        "default_range": "string",
        "slots": {"id": {"identifier": True}, "name": {"required": True}},
        "classes": classes,
    })


def test_role_edges_expand_to_every_tagged_class():
    merged = merge_ontologies("proj", _work(), _domain())
    task = merged.classes["Task"]
    assert task.slots["addresses"].any_of == ("dom_Bug",)
    assert task.slots["touches"].any_of == ("dom_Component", "dom_Bug")


def test_role_without_tagged_class_yields_no_relationship():
    work = parse_ontology({
        "name": "work", "id": "https://x/w", "default_range": "string",
        "slots": {"id": {"identifier": True}, "name": {}},
        "classes": {"Task": {"slots": ["id", "name"], "attributes": {
            "decides": {"annotations": {"edge": True, "target_role": "decision"}}}}},
    })
    merged = merge_ontologies("proj", work, _domain())
    assert "decides" not in merged.classes["Task"].slots

    schema = schema_for(merged)
    assert "DECIDES" not in schema.rel_tables


def test_bridges_are_optional():
    with pytest.raises(OntologyError, match="must be optional"):
        parse_ontology({
            "name": "work", "id": "https://x/w", "default_range": "string",
            "slots": {"id": {"identifier": True}, "name": {}},
            "classes": {"Task": {"slots": ["id", "name"], "attributes": {
                "addresses": {"annotations": {"edge": True, "target_role": "finding"}, "required": True}}}},
        })


def test_unknown_role_is_rejected_on_both_sides():
    with pytest.raises(OntologyError, match="unknown bridge_role"):
        _domain(Bad={"slots": ["id", "name"], "annotations": {"bridge_role": "server"}})


def test_domain_names_are_prefixed_so_common_names_do_not_clash():
    domain = parse_ontology({
        "name": "domain", "id": "https://x/d", "default_range": "string",
        "slots": {"id": {"identifier": True}, "name": {}},
        "classes": {"Task": {"slots": ["id", "name"]}},
    })
    merged = merge_ontologies("proj", _work(), domain)
    assert {"Task", "dom_Task"} <= set(merged.classes)


def test_prefixed_name_clash_is_still_an_error():
    work = parse_ontology({
        "name": "work", "id": "https://x/w", "default_range": "string",
        "slots": {"id": {"identifier": True}, "name": {}},
        "classes": {"dom_Bug": {"slots": ["id", "name"]}},
    })
    with pytest.raises(OntologyError, match="class names clash.*dom_Bug"):
        merge_ontologies("proj", work, _domain())


def test_invalid_prefix_is_rejected():
    with pytest.raises(OntologyError, match="domain prefix"):
        merge_ontologies("proj", _work(), _domain(), prefix="bad-prefix")


def test_domain_edges_enums_and_references_follow_the_prefix():
    domain = parse_ontology({
        "name": "domain", "id": "https://x/d", "default_range": "string",
        "enums": {"Status": {"permissible_values": {
            "open": {"annotations": {"requires": "part_of"}},
            "done": {},
        }}},
        "slots": {
            "id": {"identifier": True},
            "name": {},
            "state": {"range": "Status", "required": True},
            "part_of": {"range": "Component", "annotations": {"edge": True}},
        },
        "classes": {"Component": {"slots": ["id", "name", "state", "part_of"],
                                  "annotations": {"bridge_role": "component"}}},
    })
    merged = merge_ontologies("proj", _work(), domain)

    component = merged.classes["dom_Component"]
    assert component.slots["dom_part_of"].range == "dom_Component"
    assert component.slots["state"].range == "dom_Status"
    assert "part_of" not in component.slots
    assert merged.enums["dom_Status"].values["open"].annotations["requires"] == "dom_part_of"
    assert merged.containment == "part_of"  # work containment stays the merged containment
    assert "dom_Component" in merged.classes["Task"].slots["touches"].any_of


def test_merged_identifier_must_match():
    other = parse_ontology({
        "name": "domain", "id": "https://x/d", "default_range": "string",
        "slots": {"key": {"identifier": True}},
        "classes": {"Component": {"slots": ["key"], "annotations": {"bridge_role": "component"}}},
    })
    with pytest.raises(OntologyError, match="identifier"):
        merge_ontologies("proj", _work(), other)


def test_merged_schema_shares_same_named_relationship_tables():
    merged = merge_ontologies("proj", _work(), _domain())
    schema = schema_for(merged)
    assert ("Task", "dom_Bug") in schema.rel_tables["ADDRESSES"].pairs
    assert ("Task", "dom_Component") in schema.rel_tables["TOUCHES"].pairs


def test_load_project_reads_manifest_and_merges(tmp_path):
    from memory_engine.core.project import load_project

    (tmp_path / "work.yaml").write_text(
        "id: https://x/w\nname: work\ndefault_range: string\n"
        "slots:\n  id: {identifier: true}\n  name: {}\n"
        "classes:\n  Task: {slots: [id, name]}\n", encoding="utf-8")
    (tmp_path / "domain.yaml").write_text(
        "id: https://x/d\nname: domain\ndefault_range: string\n"
        "slots:\n  id: {identifier: true}\n  name: {}\n"
        "classes:\n  Component: {slots: [id, name], annotations: {bridge_role: component}}\n",
        encoding="utf-8")
    manifest = tmp_path / "payments.yaml"
    manifest.write_text(
        "name: payments\nwork: work.yaml\ndomain: domain.yaml\ndomain_prefix: area\n", encoding="utf-8")

    ontology = load_project(manifest)
    assert ontology.name == "payments"
    assert {"Task", "area_Component"} <= set(ontology.classes)
    assert ontology.annotations["modules"]["domain_prefix"] == "area"


def test_settings_project_mode_excludes_ontology(tmp_path, monkeypatch):
    import pytest

    from memory_engine.config import Settings

    manifest = tmp_path / "p.yaml"
    manifest.write_text("name: p\ndomain: architecture\nwork: workgraph\n", encoding="utf-8")
    monkeypatch.delenv("MEMORY_PROJECT", raising=False)
    monkeypatch.delenv("MEMORY_ONTOLOGY", raising=False)

    settings = Settings.from_env(project=manifest)
    assert settings.project_path == manifest.resolve()
    assert settings.ontology_path is None

    with pytest.raises(ValueError, match="either --ontology or --project"):
        Settings.from_env("workgraph", project=manifest)


def test_bare_domain_class_name_resolves_to_prefixed_class():
    merged = merge_ontologies("proj", _work(), _domain())
    assert merged.get_class("Component").name == "dom_Component"
    assert merged.get_class("dom_Component").name == "dom_Component"
    assert merged.get_class("task").name == "Task"  # work classes are never prefixed


def test_exact_name_wins_over_prefixed_match():
    work = parse_ontology({
        "name": "work", "id": "https://x/w", "default_range": "string",
        "slots": {"id": {"identifier": True}, "name": {}},
        "classes": {"Component": {"slots": ["id", "name"]}},
    })
    merged = merge_ontologies("proj", work, _domain())
    assert merged.get_class("Component").name == "Component"


def test_project_skill_is_self_contained(tmp_path):
    from memory_engine.core.project import load_project
    from memory_engine.core.skill import build_project_skill

    (tmp_path / "work.yaml").write_text(
        "id: https://x/w\nname: work\ndefault_range: string\n"
        "slots:\n  id: {identifier: true}\n  name: {}\n"
        "classes:\n  Task: {slots: [id, name]}\n", encoding="utf-8")
    (tmp_path / "domain.yaml").write_text(
        "id: https://x/d\nname: domain\ndefault_range: string\n"
        "slots:\n  id: {identifier: true}\n  name: {}\n"
        "classes:\n  Component: {slots: [id, name], annotations: {bridge_role: component}}\n",
        encoding="utf-8")
    manifest = tmp_path / "payments.yaml"
    manifest.write_text("name: payments\nwork: work.yaml\ndomain: domain.yaml\n", encoding="utf-8")

    out = build_project_skill(manifest, tmp_path / "skills" / "payments-memory")
    assert (out / "references" / "work.yaml").is_file()
    assert (out / "references" / "domain.yaml").is_file()
    assert "MEMORY_PROJECT" in (out / "scripts" / "mem").read_text()
    assert "MEMORY_ONTOLOGY" not in (out / "scripts" / "mem").read_text().replace("unset MEMORY_ONTOLOGY", "")
    assert "## Work and domain" in (out / "SKILL.md").read_text()

    # the copied manifest is self-contained: it loads on its own, from the skill directory
    copied = load_project(out / "references" / "project.yaml")
    assert {"Task", "dom_Component"} <= set(copied.classes)


def test_standalone_ontology_hides_unresolved_bridges(tmp_path):
    from memory_engine.core.skill import build_skill

    ontology = parse_ontology({
        "name": "work", "id": "https://x/w", "default_range": "string",
        "slots": {"id": {"identifier": True}, "name": {}},
        "classes": {"Task": {"slots": ["id", "name"], "attributes": {
            "addresses": {"annotations": {"edge": True, "target_role": "finding"}, "multivalued": True}}}},
    })
    assert "addresses" not in {s.name for s in ontology.classes["Task"].edges}
    assert "addresses" in {s.name for s in ontology.classes["Task"].bridges}

    src = tmp_path / "work.yaml"
    src.write_text(
        "id: https://x/w\nname: work\ndefault_range: string\n"
        "slots:\n  id: {identifier: true}\n  name: {}\n"
        "classes:\n  Task:\n    slots: [id, name]\n    attributes:\n"
        "      addresses: {annotations: {edge: true, target_role: finding}, multivalued: true}\n",
        encoding="utf-8")
    out = build_skill(src, tmp_path / "skills" / "work-memory")
    assert "## Work and domain" not in (out / "SKILL.md").read_text()
    assert "addresses" not in (out / "SKILL.md").read_text()


def _manifest_with_db(tmp_path, db_line, name="payments.yaml"):
    (tmp_path / "work.yaml").write_text(
        "id: https://x/w\nname: work\ndefault_range: string\n"
        "slots:\n  id: {identifier: true}\n  name: {}\n"
        "classes:\n  Task: {slots: [id, name]}\n", encoding="utf-8")
    (tmp_path / "domain.yaml").write_text(
        "id: https://x/d\nname: domain\ndefault_range: string\n"
        "slots:\n  id: {identifier: true}\n  name: {}\n"
        "classes:\n  Component: {slots: [id, name], annotations: {bridge_role: component}}\n",
        encoding="utf-8")
    manifest = tmp_path / "project" / name
    manifest.parent.mkdir(exist_ok=True)
    manifest.write_text(f"name: payments\nwork: ../work.yaml\ndomain: ../domain.yaml\n{db_line}", encoding="utf-8")
    return manifest


def test_manifest_db_resolves_next_to_the_manifest(tmp_path):
    from memory_engine.config import load_manifest

    manifest = _manifest_with_db(tmp_path, "db: ./memory/payments.lbdb\n")
    assert load_manifest(manifest).db == str((manifest.parent / "memory" / "payments.lbdb").resolve())


def test_manifest_db_absolute_and_memory_are_kept(tmp_path):
    from memory_engine.config import load_manifest

    absolute = tmp_path / "abs.lbdb"
    assert load_manifest(_manifest_with_db(tmp_path, f"db: {absolute}\n")).db == str(absolute)
    assert load_manifest(_manifest_with_db(tmp_path, 'db: ":memory:"\n')).db == ":memory:"


def test_manifest_db_must_be_a_path(tmp_path):
    import pytest

    from memory_engine.config import load_manifest
    from memory_engine.core.errors import OntologyError

    with pytest.raises(OntologyError, match="`db`"):
        load_manifest(_manifest_with_db(tmp_path, "db: [1, 2]\n"))


def test_database_precedence_flag_then_manifest_then_data_dir(tmp_path, monkeypatch):
    from memory_engine.config import Settings, default_db_path

    manifest = _manifest_with_db(tmp_path, "db: ./payments.lbdb\n")
    monkeypatch.delenv("MEMORY_DB_PATH", raising=False)
    monkeypatch.delenv("MEMORY_PROJECT", raising=False)
    monkeypatch.delenv("MEMORY_ONTOLOGY", raising=False)

    assert Settings.from_env(project=manifest).db_path == str((manifest.parent / "payments.lbdb").resolve())

    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "env.lbdb"))
    assert Settings.from_env(project=manifest).db_path == str(tmp_path / "env.lbdb")

    assert Settings.from_env(project=manifest, db_path=tmp_path / "flag.lbdb").db_path == str(tmp_path / "flag.lbdb")

    monkeypatch.delenv("MEMORY_DB_PATH")
    no_db = _manifest_with_db(tmp_path, "", name="plain.yaml")
    assert Settings.from_env(project=no_db).db_path is None  # the data-dir default applies later
    assert default_db_path("payments").name == "payments.lbdb"


def test_project_skill_copy_carries_the_resolved_database_path(tmp_path, monkeypatch):
    import yaml

    from memory_engine.config import load_manifest
    from memory_engine.core.skill import build_project_skill

    manifest = _manifest_with_db(tmp_path, "db: ./payments.lbdb\n")
    out = build_project_skill(manifest, tmp_path / "skills" / "payments-memory")
    copied = yaml.safe_load((out / "references" / "project.yaml").read_text())
    assert copied["db"] == str((manifest.parent / "payments.lbdb").resolve())

    monkeypatch.chdir(tmp_path)  # loading the copy from elsewhere gives the same database path
    assert load_manifest(out / "references" / "project.yaml").db == copied["db"]
