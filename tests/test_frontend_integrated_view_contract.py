from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_asset(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_customer_service_choice_has_integrated_view_button():
    app_js = read_asset("app/static/js/app.js")

    assert 'const INTEGRATED_SERVICE = "integrated";' in app_js
    assert 'data-service="${INTEGRATED_SERVICE}">통합보기</button>' in app_js
    assert 'function validCustomerViewType(serviceType)' in app_js


def test_integrated_view_renders_both_customer_service_cards():
    app_js = read_asset("app/static/js/app.js")

    assert 'function renderViewerCallBody(scope, selectedService)' in app_js
    assert 'SERVICE_ORDER.map((serviceType) => renderCustomerDisplayServiceCard(serviceType, scope)).join("")' in app_js
    assert 'selectedServiceMatchesCall(appState.selectedCustomerService, call.service_type)' in app_js
    assert 'selectedServiceMatchesCall(appState.selectedDisplayService, call.service_type)' in app_js


def test_customer_viewer_controls_are_hidden_behind_gear_modal():
    app_js = read_asset("app/static/js/app.js")

    assert 'function renderViewerGear(scope)' in app_js
    assert 'function renderViewerSettingsModal(scope)' in app_js
    assert 'data-action="openViewerSettings"' in app_js
    assert 'data-action="enableVoice">${appState.voiceEnabled ? "음성 켜짐" : "음성 시작"}</button>' in app_js
    assert 'data-action="${changeServiceAction}">업무 변경</button>' in app_js
    assert 'data-action="${changeStoreAction}">매장 변경</button>' in app_js


def test_integrated_view_uses_responsive_two_column_css():
    style_css = read_asset("app/static/css/style.css")

    assert '.btn-green' in style_css
    assert '.integrated-service-wrap' in style_css
    assert 'grid-template-columns: repeat(2, minmax(0, 1fr));' in style_css
    assert '.grid-two, .integrated-service-wrap { grid-template-columns: 1fr; }' in style_css
