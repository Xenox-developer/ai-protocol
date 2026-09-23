use super::*;
use serde_json::json;

#[test]
fn owner_mapping_validates_routes_schemas_permissions_and_shared_budgets() {
    let text = include_str!("../services/support.json");
    let service = service::Service::parse(text).unwrap();
    let identities = service
        .identities(|name| Ok(format!("test-{name}")))
        .unwrap();
    let full = &identities["test-SUPPORT_AGENT_TOKEN"];
    let limited = &identities["test-SUPPORT_READ_TOKEN"];
    assert!(Arc::ptr_eq(&full.budget, &limited.budget));
    assert!(!Arc::ptr_eq(
        &full.budget,
        &identities["test-SUPPORT_INTERACTIVE_TOKEN"].budget
    ));
    assert_eq!(limited.operations, ["get_ticket"]);
    let op = &service.operations[0];
    let request = op
        .request(&json!({"query":"reset & ticket_id=102"}))
        .unwrap();
    assert_eq!(request.path, "/api/tickets/search");
    assert_eq!(
        request.query,
        [("text".into(), "reset & ticket_id=102".into())]
    );
    for invalid in [
        json!({}),
        json!({"query":1}),
        json!({"query":"", "url":"http://evil"}),
        json!([]),
    ] {
        assert!(op.request(&invalid).is_err());
    }
    assert!(service.operations[1].request(&json!({"id":-1})).is_err());
    assert!(service.operations[1].request(&json!({"id":true})).is_err());
    assert_eq!(
        op.descriptor()["input_schema"]["additionalProperties"],
        false
    );
    for path in [
        "//evil",
        "/../admin",
        "/api?url=x",
        "http://evil",
        "/api/%2f",
        "/{route}",
    ] {
        let mut manifest: serde_json::Value = serde_json::from_str(text).unwrap();
        manifest["operations"][0]["upstream_path"] = json!(path);
        assert!(service::Service::parse(&manifest.to_string()).is_err());
        manifest["operations"][0]["upstream_path"] = json!("/valid");
        manifest["operations"][0]["path"] = json!(path);
        assert!(service::Service::parse(&manifest.to_string()).is_err());
    }
    for url in [
        "file:///tmp/x",
        "http://user:secret@localhost",
        "http://localhost/a",
        "http://localhost/?q=x",
        "http://localhost/#x",
    ] {
        assert!(service::upstream_origin(url.into()).is_err());
    }
    for (field, value) in [
        ("max_outstanding", json!(2)),
        ("operations", json!(["unknown"])),
    ] {
        let mut manifest: serde_json::Value = serde_json::from_str(text).unwrap();
        manifest["credentials"][1][field] = value;
        assert!(service::Service::parse(&manifest.to_string()).is_err());
    }
    // Equal principal IDs do not unify budgets belonging to different service instances.
    let catalog = service::Service::catalog()
        .identities(|name| Ok(format!("test-{name}")))
        .unwrap();
    assert_eq!(
        catalog["test-AGENT_TOKEN_1"].principal_id,
        full.principal_id
    );
    full.budget.set_maximum(2);
    assert_eq!(catalog["test-AGENT_TOKEN_1"].budget.snapshot().maximum, 5);
}
