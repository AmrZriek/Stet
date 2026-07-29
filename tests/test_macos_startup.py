import types

import stet.core.macos_startup as startup


class FakeService:
    def __init__(self, status):
        self.current_status = status
        self.calls = []

    def status(self):
        return self.current_status

    def registerAndReturnError_(self):
        self.calls.append("register")
        self.current_status = 1
        return True, None

    def unregisterAndReturnError_(self):
        self.calls.append("unregister")
        self.current_status = 0
        return True, None


class FakeServiceManagement:
    SMAppServiceStatusNotRegistered = 0
    SMAppServiceStatusEnabled = 1
    SMAppServiceStatusRequiresApproval = 2
    SMAppServiceStatusNotFound = 3
    SMAppService = types.SimpleNamespace()


def framework_for(service):
    framework = FakeServiceManagement
    framework.SMAppService = types.SimpleNamespace(mainApp=lambda: service)
    return framework


def test_non_mac_query_is_safe(monkeypatch):
    monkeypatch.setattr(startup.sys, "platform", "linux")

    state = startup.query_login_item()

    assert state.supported is False
    assert state.status is startup.LoginItemStatus.NOT_APPLICABLE


def test_query_maps_smappservice_states(monkeypatch):
    monkeypatch.setattr(startup.sys, "platform", "darwin")
    service = FakeService(2)

    state = startup.query_login_item(framework_for(service))

    assert state.supported is True
    assert state.status is startup.LoginItemStatus.REQUIRES_APPROVAL


def test_enable_and_disable_use_native_registration(monkeypatch):
    monkeypatch.setattr(startup.sys, "platform", "darwin")
    service = FakeService(0)
    framework = framework_for(service)

    enabled = startup.enable_login_item(framework)
    disabled = startup.disable_login_item(framework)

    assert enabled.status is startup.LoginItemStatus.ENABLED
    assert disabled.status is startup.LoginItemStatus.DISABLED
    assert service.calls == ["register", "unregister"]


def test_registration_error_is_reported_as_denied(monkeypatch):
    monkeypatch.setattr(startup.sys, "platform", "darwin")
    error = types.SimpleNamespace(localizedDescription="approval was denied")

    class RejectingService(FakeService):
        def registerAndReturnError_(self):
            return False, error

    state = startup.enable_login_item(framework_for(RejectingService(0)))

    assert state.status is startup.LoginItemStatus.DENIED
    assert state.detail == "approval was denied"
