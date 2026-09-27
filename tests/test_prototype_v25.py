from pathlib import Path
import re


PROTOTYPE = Path(__file__).parents[1] / "web" / "TripSense_产品原型_v2.0.html"


def source() -> str:
    return PROTOTYPE.read_text(encoding="utf-8")


def test_v25_duration_selector_supports_custom_days():
    html = source()
    assert "手机端旅行助手原型 v2.5" in html
    assert len(re.findall(r'<button class="[^"]*day-choice', html)) == 6
    assert 'data-value="custom"' in html
    assert 'id="custom-days"' in html
    assert 'id="custom-days-minus"' in html
    assert 'id="custom-days-plus"' in html
    assert "function setCustomDays" in html


def test_redundant_home_actions_are_removed():
    html = source()
    assert 'id="setup-home"' not in html
    assert 'id="saved-home"' not in html
    assert "回到首页" not in html


def test_saved_trip_supports_switching_and_direct_recording_copy():
    html = source()
    assert 'id="trip-switch"' in html
    assert 'id="trip-switch-dialog"' in html
    assert "showModal()" in html
    assert "旅途中也可以为去过的地点添加照片、文字和心情，记录自己的旅程" in html
    assert 'id="saved-chat"' in html
    assert "调整这份行程" in html
    assert 'id="saved-switch-bottom"' in html


def test_saved_stops_reuse_duration_wheel():
    html = source()
    render_saved = html.split("function renderSaved()", 1)[1].split("function toast()", 1)[0]
    assert "durationMenu(s)" in render_saved
    assert "bindDurationMenus" in render_saved


def test_navigation_uses_consistent_line_icon_set():
    html = source()
    for icon in ("home", "compass", "sparkles", "map", "user"):
        assert f'id="icon-{icon}"' in html
        assert f'href="#icon-{icon}"' in html
    assert "♡♡" not in html
    assert ">◇<" not in html
    assert ">▤<" not in html
    assert ">☺<" not in html


def test_home_language_is_general_and_entry_colors_are_equal_weight():
    html = source()
    assert "想换地点，还是调整节奏？" in html
    assert "继续规划最近的上海三日行程" in html
    assert "这份行程，还想调整哪里？" not in html
    assert "或问问雨天" not in html
    assert 'class="home-entry" id="new-trip"' in html
    assert 'class="home-entry" id="home-ai"' in html


def test_long_custom_trips_explain_partial_prototype_coverage():
    html = source()
    assert 'id="route-coverage-note"' in html
    assert 'id="saved-coverage-note"' in html
    assert "当前展示 3 / "+"'" in html
    assert "可以调整已有安排" in html
    assert "slowOption.querySelector('b').textContent" in html


def test_day_tabs_keep_horizontal_scroll_without_visible_scrollbar():
    html = source()
    assert ".day-tabs{scrollbar-width:none}" in html
    assert ".day-tabs::-webkit-scrollbar{display:none}" in html


def test_home_hero_has_rich_fallback_when_remote_photo_is_unavailable():
    html = source()
    assert "background-color:#302940" in html


def test_setup_copy_is_concise_and_frames_inspiration_as_recommended_styles():
    html = source()
    assert "先从最容易回答的开始。日期可以以后再定，路线也随时能改。" not in html
    assert "1–5 天快速选择，更多天数可以自定义" not in html
    assert "可选择 6–30 天" not in html
    assert "后续也能在聊天中继续增减天数" not in html
    assert "先看看为你推荐的旅行风格" in html
    assert 'id="setup-next">选择旅行风格<' in html


def test_header_is_opaque_while_scrolling():
    html = source()
    assert ".top,.top.home-bar{height:54px;background:#fffdf9" in html


def test_profile_rows_use_a_larger_consistent_chevron():
    html = source()
    assert 'id="icon-chevron-right"' in html
    assert html.count('class="icon profile-chevron"') == 3
    assert ".profile-chevron{width:20px;height:20px" in html


def test_topic_suggestions_stay_with_composer_and_only_update_on_topic_change():
    html = source()
    assert 'class="chat-tools"' in html
    assert ".chat-tools{position:sticky;bottom:70px" in html
    assert ".quick{flex-wrap:nowrap" in html
    assert "let activeSuggestionTopic" in html
    assert "function detectSuggestionTopic" in html
    send_body = html.split("function send()", 1)[1].split("function renderTabs()", 1)[0]
    assert "detectSuggestionTopic(t)" in send_body
    assert "setSuggestions('initial')" not in send_body


def test_chat_uses_the_backend_model_and_keeps_a_local_fallback():
    html = source()
    send_body = html.split("function send()", 1)[1].split("function renderTabs()", 1)[0]
    assert "await apiClient.chat" in send_body
    assert "response.message" in send_body
    assert "response.plan" in send_body
    assert "catch" in send_body
    assert "ensureBackendPlan" in html
    assert "dayWordForPrompt" in html
    assert "哪个地方一定要留" not in html
    assert "后面还能改" not in html
    assert "时间是参考，按你当天的状态走就好" not in html
    assert "三天各有主题" not in html
    assert "routeHeroCopy" in html
    assert "哪天想松一点" in html
    assert "这只是参考节奏，可按当天状态轻轻微调" in html


def test_live_weather_builds_a_preview_before_applying_a_new_route():
    html = source()
    assert "async function handleWeatherAction" in html
    assert "apply_realtime:true" in html
    assert "weatherPreviewStops=mapBackendStops" in html
    assert "weatherDemoState?.routeAdjusted" in html


def test_cold_start_introduces_product_then_recommends_the_detected_city():
    html = source()
    assert 'id="cold-start"' in html
    assert 'id="cold-guest"' in html
    assert 'id="cold-auth"' in html
    assert 'id="cold-intro"' in html
    assert 'id="cold-discovery"' in html
    assert "TripSense，陪你定制每一段独特旅程。" in html
    assert "旅行灵感" in html
    assert 'data-cold-city="上海"' in html
    assert "正在准备你的上海旅行" not in html
    assert ".cold-open>.top" in html
    assert 'id="account-control"' in html
    assert "resolveLaunchState" in html


def test_inspiration_is_direct_and_trip_context_is_edited_in_place():
    html = source()
    assert 'id="inspire-context-edit"' in html
    assert 'id="route-context-edit"' in html
    assert 'id="trip-context-dialog"' in html
    assert "function openInspiration" in html
    assert "$('#new-trip').onclick=()=>openInspiration('view-home')" in html
    assert "else if(id==='view-inspire'){openInspiration('view-home')}" in html


def test_chat_reading_area_and_composer_share_readable_type_size():
    html = source()
    assert ".messages{height:clamp(440px,55dvh,560px)" in html
    assert ".msg{max-width:90%;font-size:13px" in html
    assert ".composer input{font-size:13px;line-height:1.55" in html


def test_new_style_chat_uses_toast_instead_of_pinned_saved_route():
    html = source()
    assert 'id="saved-route-toast"' in html
    assert "已有一份保存行程，点击查看" in html
    assert "function showSavedTripHint" in html
    assert "$('#start-chat').onclick" in html
    assert "startChat(false)" in html
    assert "resetDraftPlan" in html


def test_ai_entry_points_share_the_sparkles_identity():
    html = source()
    home_ai = html.split('id="home-ai"', 1)[1].split('</button>', 1)[0]
    assert 'href="#icon-sparkles"' in home_ai
    nav_ai = html.split('class="nav-btn ai"', 1)[1].split('</button>', 1)[0]
    assert 'href="#icon-sparkles"' in nav_ai


def test_saved_route_chat_notice_is_dismissible_and_has_no_duplicate_save_action():
    html = source()
    assert 'id="chat-route-dismiss"' in html
    assert "function configureChatRouteNotice" in html
    assert "查看当前路线" in html
    assert "chatRouteSaved" in html
    assert "dismissChatRouteNotice" in html


def test_chat_route_updates_surface_a_new_inline_snapshot():
    html = source()
    start_body = html.split("async function startChat", 1)[1].split("$('#chat-route-dismiss')", 1)[0]
    send_body = html.split("async function send()", 1)[1].split("function renderTabs()", 1)[0]
    snapshot_body = html.split("function appendRouteSnapshot", 1)[1].split("async function startChat", 1)[0]
    assert "appendRouteSnapshot(backendPlan" in start_body
    assert "appendRouteSnapshot(response.plan,state)" in send_body
    assert "retireRouteSnapshotActions()" in snapshot_body
    assert "dismissChatRouteNotice()" in snapshot_body
    assert "data-route-snapshot-open" in snapshot_body
    assert "data-route-snapshot-save" in snapshot_body
    assert "scrollIntoView" in snapshot_body
    assert "@media(prefers-reduced-motion:reduce)" in html


def test_journey_record_allows_a_custom_mood():
    html = source()
    assert 'id="custom-mood-toggle"' in html
    assert 'id="custom-mood-input"' in html
    assert 'id="custom-mood-save"' in html
    assert "function saveCustomMood" in html


def test_saved_trip_footer_can_switch_routes_and_copy_describes_free_editing():
    html = source()
    assert 'id="saved-switch-bottom"' in html
    assert "function openTripSwitcher" in html
    assert "调整已有安排，也可以继续规划其他天数" in html
    assert "补齐剩余安排" not in html


def test_ai_travel_diary_combines_route_and_records_into_a_scrapbook_view():
    html = source()
    assert 'id="ai-diary-nudge"' in html
    assert 'id="view-diary"' in html
    assert 'id="generate-ai-diary"' in html
    assert 'id="open-ai-diary"' in html
    assert "AI 旅行日记" in html
    assert "路线与记录" in html
    assert "function generateAiDiary" in html


def test_route_stop_has_a_discoverable_place_detail_entry():
    html = source()
    assert 'class="place-detail-link"' in html
    assert "了解地点" in html
    assert "出发前了解" in html
    assert "place-cultural-preview" in html


def test_saved_trip_stops_expose_the_same_place_detail_flow():
    html = source()
    render_saved = html.split("function renderSaved()", 1)[1].split("function toast()", 1)[0]
    assert "data-saved-place-detail" in render_saved
    assert "了解地点" in render_saved
    assert "openPlaceDetail" in render_saved


def test_place_detail_view_explains_the_recommendation_and_cultural_context():
    html = source()
    assert 'id="view-place-detail"' in html
    assert 'id="place-detail-back"' in html
    assert 'id="place-detail-why"' in html
    assert 'id="place-detail-culture"' in html
    assert "为什么推荐给你" in html
    assert "认识这里" in html
    assert "function openPlaceDetail" in html
    assert "function closePlaceDetail" in html


def test_place_detail_uses_curated_content_and_preserves_route_context():
    html = source()
    assert "const placeDetails=" in html
    assert "上海博物馆" in html
    assert "南京路步行街" in html
    assert "外滩" in html
    assert "placeDetailReturnScroll" in html
    assert "资料已核验" in html
