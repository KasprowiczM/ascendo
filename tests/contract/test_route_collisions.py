"""Route-collision test: no stub endpoint may shadow a real route.

The dashboard mounts routers in a specific order. Comment-only ordering
contracts are brittle. This test inspects the resolved route table and
asserts that no real (non-stub) endpoint path is also claimed by a stub.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from ascendo.dashboard import create_app


def _make_client(tmp_path) -> TestClient:
    from .test_dashboard import FakeAdapter

    return TestClient(create_app(adapter=FakeAdapter(), runs_dir=tmp_path))


def test_no_stub_shadows_real_route(tmp_path) -> None:
    """Collect all route paths, verify no stub tag coexists with a real tag."""
    client = _make_client(tmp_path)
    app = client.app

    stub_paths: set[tuple[str, str]] = set()
    real_paths: set[tuple[str, str]] = set()

    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        tags = getattr(route, "tags", None) or []
        if path is None:
            continue
        method_set = methods or {"GET"}
        is_stub = "spa-stubs" in tags
        for m in method_set:
            key = (m.upper(), path)
            if is_stub:
                stub_paths.add(key)
            else:
                real_paths.add(key)

    collisions = stub_paths & real_paths
    assert not collisions, (
        f"Stub routes shadow real routes: {sorted(collisions)}. "
        "Remove the stub(s) from spa_stubs.py."
    )


def test_all_stub_routes_have_tags(tmp_path) -> None:
    """Every spa_stubs route must carry the 'spa-stubs' tag for detection."""
    from ascendo.dashboard.routes.spa_stubs import router as stub_router

    for route in stub_router.routes:
        tags = getattr(route, "tags", None) or []
        assert "spa-stubs" in tags or not hasattr(route, "endpoint"), (
            f"Stub route {getattr(route, 'path', '?')} missing 'spa-stubs' tag"
        )
