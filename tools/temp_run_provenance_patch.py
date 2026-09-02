from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"expected one integration point in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "jellyfin_show_organizer/reports.py",
    "    sidecars_csv: bytes\n    preflight_json: bytes | None = None\n",
    "    sidecars_csv: bytes\n    run_provenance_json: bytes | None = None\n"
    "    preflight_json: bytes | None = None\n",
)
replace_once(
    "jellyfin_show_organizer/reports.py",
    "        if self.preflight_txt is not None:\n"
    "            files.append((\"preflight.txt\", self.preflight_txt))\n"
    "        files.append((\"plan.json\", self.plan_json))\n",
    "        if self.preflight_txt is not None:\n"
    "            files.append((\"preflight.txt\", self.preflight_txt))\n"
    "        if self.run_provenance_json is not None:\n"
    "            files.append((\"run-provenance.json\", self.run_provenance_json))\n"
    "        files.append((\"plan.json\", self.plan_json))\n",
)
replace_once(
    "jellyfin_show_organizer/reports.py",
    "def render_audit_bundle(\n"
    "    plan: OrganizerPlan,\n"
    "    preflight: PreflightResult | None = None,\n"
    ") -> AuditBundle:\n",
    "def render_audit_bundle(\n"
    "    plan: OrganizerPlan,\n"
    "    preflight: PreflightResult | None = None,\n"
    "    *,\n"
    "    run_provenance_json: bytes | None = None,\n"
    ") -> AuditBundle:\n",
)
replace_once(
    "jellyfin_show_organizer/reports.py",
    "        sidecars_csv=render_sidecars_csv(plan),\n"
    "        summary_txt=render_summary(plan, preflight),\n",
    "        sidecars_csv=render_sidecars_csv(plan),\n"
    "        run_provenance_json=run_provenance_json,\n"
    "        summary_txt=render_summary(plan, preflight),\n",
)
replace_once(
    "jellyfin_show_organizer/reports.py",
    "def write_audit_bundle(\n"
    "    output_dir: Path,\n"
    "    plan: OrganizerPlan,\n"
    "    preflight: PreflightResult | None = None,\n"
    ") -> AuditBundle:\n",
    "def write_audit_bundle(\n"
    "    output_dir: Path,\n"
    "    plan: OrganizerPlan,\n"
    "    preflight: PreflightResult | None = None,\n"
    "    *,\n"
    "    run_provenance_json: bytes | None = None,\n"
    ") -> AuditBundle:\n",
)
replace_once(
    "jellyfin_show_organizer/reports.py",
    "    bundle = render_audit_bundle(plan, preflight)\n",
    "    bundle = render_audit_bundle(\n"
    "        plan, preflight, run_provenance_json=run_provenance_json\n"
    "    )\n",
)

replace_once(
    "jellyfin_show_organizer/planner.py",
    "from .reports import AuditBundle, write_audit_bundle\n"
    "from .schema import PLAN_SCHEMA_VERSION, stable_plan_hash\n",
    "from .reports import AuditBundle, write_audit_bundle\n"
    "from .run_provenance import (\n"
    "    build_run_provenance,\n"
    "    detect_source_revision,\n"
    "    render_run_provenance,\n"
    ")\n"
    "from .schema import PLAN_SCHEMA_VERSION, stable_plan_hash\n",
)
replace_once(
    "jellyfin_show_organizer/planner.py",
    "    bundle = write_audit_bundle(output_dir, plan, preflight)\n"
    "    provider_failure = any(\n"
    "        record.state is not CacheState.OK for record in cache.records.values()\n"
    "    )\n",
    "    provider_failure = any(\n"
    "        record.state is not CacheState.OK for record in cache.records.values()\n"
    "    )\n"
    "    provider_mode = (\n"
    "        \"offline\" if config.offline else \"refresh\" if config.refresh else \"online\"\n"
    "    )\n"
    "    run_provenance = build_run_provenance(\n"
    "        plan,\n"
    "        source_revision=detect_source_revision(),\n"
    "        provider_mode=provider_mode,\n"
    "        provider_failure=provider_failure,\n"
    "        max_path_length=config.max_path_length,\n"
    "        max_component_length=config.max_component_length,\n"
    "        overrides_configured=config.overrides_path is not None,\n"
    "        preflight_ready=preflight.ready,\n"
    "        preflight_finding_count=len(preflight.findings),\n"
    "    )\n"
    "    bundle = write_audit_bundle(\n"
    "        output_dir,\n"
    "        plan,\n"
    "        preflight,\n"
    "        run_provenance_json=render_run_provenance(run_provenance),\n"
    "    )\n",
)

replace_once(
    "tests/local/test_planner.py",
    "        \"preflight.txt\",\n"
    "        \"sidecars.csv\",\n",
    "        \"preflight.txt\",\n"
    "        \"run-provenance.json\",\n"
    "        \"sidecars.csv\",\n",
)
replace_once(
    "tests/local/test_planner.py",
    "    assert output_files == {\n",
    "    assert output_files == {\n",
)
replace_once(
    "tests/local/test_planner.py",
    "    def reject_network(\n",
    "    provenance_payload = json.loads(\n"
    "        (tmp_path / \"audit-first\" / \"run-provenance.json\").read_text(\n"
    "            encoding=\"utf-8\"\n"
    "        )\n"
    "    )\n"
    "    assert provenance_payload[\"plan_sha256\"] == first.preflight.plan_hash\n"
    "    assert provenance_payload[\"provider\"][\"mode\"] == \"online\"\n"
    "    assert provenance_payload[\"provider\"][\"cache_snapshot_count\"] == 2\n"
    "    assert str(tmp_path) not in json.dumps(provenance_payload)\n\n"
    "    def reject_network(\n",
)
