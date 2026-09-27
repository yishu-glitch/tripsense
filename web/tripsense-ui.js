(function (root, factory) {
  const exports = factory();
  if (root) root.TripSenseUi = exports;
  if (typeof module !== "undefined" && module.exports) module.exports = exports;
})(typeof window !== "undefined" ? window : globalThis, function () {
  function countRecordedJourneys(recordsByJourney) {
    return Object.values(recordsByJourney || {}).filter(
      (recordCount) => Number(recordCount) > 0,
    ).length;
  }

  function filterTripsByCity(trips, city) {
    const items = Array.isArray(trips) ? trips : [];
    if (!city || city === "全部") return items.slice();
    return items.filter((trip) => trip.city === city);
  }

  function resolveLaunchState(options) {
    const state = options || {};
    const firstVisit = Boolean(state.forceFirst || !state.hasVisited);
    const city =
      (state.hasVisited && state.lastCity) ||
      state.detectedCity ||
      state.lastCity ||
      state.fallbackCity ||
      "上海";

    return {
      firstVisit,
      city,
      signedIn: Boolean(state.signedIn),
    };
  }

  function summarizeTripContext(trip) {
    const context = trip || {};
    const city = context.city || "上海";
    const companion = context.companion || "同行未设置";
    const days = Number(context.days) > 0 ? `${Number(context.days)} 天` : "天数未设置";
    const date = context.date || "日期待定";
    return [city, companion, days, date].join(" · ");
  }

  function tripContextReady(trip) {
    const context = trip || {};
    return Boolean(context.companion && Number(context.days) > 0);
  }

  function resolveWeatherDemo(search) {
    const params = new URLSearchParams(search || "");
    if (params.get("demo_weather") !== "rain") return null;
    return {
      kind: "rain",
      stage: "notice",
      routeAdjusted: false,
    };
  }

  function transitionWeatherDemo(current, action) {
    if (!current || current.kind !== "rain") return current;
    const next = { ...current };
    if (action === "preview") next.stage = "preview";
    if (action === "back" || action === "reopen") next.stage = "notice";
    if (action === "keep") next.stage = "kept";
    if (action === "apply") {
      next.stage = "applied";
      next.routeAdjusted = true;
    }
    if (action === "undo") {
      next.stage = "notice";
      next.routeAdjusted = false;
    }
    return next;
  }

  function weatherNoticeFromPlan(plan, cityLabel) {
    const notices = Array.isArray(plan?.realtime_notices) ? plan.realtime_notices : [];
    const notice = notices.find(
      (item) => item?.category === "weather" && item?.requires_confirmation !== false,
    );
    if (!notice) return null;
    const lead = String(notice.message || "天气可能变化").split("，")[0].replace(/[。？?]$/, "");
    return {
      kind: "rain",
      stage: "notice",
      routeAdjusted: false,
      live: true,
      source: notice.source || "realtime",
      title: `${cityLabel || "当前城市"}${lead}`,
      message: String(notice.message || ""),
    };
  }

  function weatherDemoPresentation(current) {
    if (!current || current.kind !== "rain") return null;
    if (current.stage === "preview") {
      return {
        compact: false,
        kicker: "调整预览 · 尚未应用",
        title: current.live ? "根据实时提醒生成了备选路线" : "把 D1 的两段户外安排换到室内",
        copy: "当前路线仍保持原样，确认后才会替换。",
        actions: [
          { action: "back", label: "返回", ariaLabel: "返回天气提醒" },
          { action: "apply", label: "确认应用", ariaLabel: "确认应用雨天方案" },
        ],
      };
    }
    if (current.stage === "applied") {
      return {
        compact: true,
        kicker: "已按雨天调整",
        title: "D1 已换成室内路线",
        copy: "其余安排保持不变。",
        actions: [{ action: "undo", label: "↶", ariaLabel: "撤销雨天调整" }],
      };
    }
    if (current.stage === "kept") {
      return {
        compact: true,
        kicker: "天气提醒",
        title: "已保持原路线",
        copy: "需要时仍可查看雨天方案。",
        actions: [{ action: "reopen", label: "查看方案", ariaLabel: "重新查看雨天方案" }],
      };
    }
    return {
      compact: true,
      kicker: current.live ? "天气提醒 · 高德实时" : "天气提醒 · 演示数据",
      title: current.title || "上海当前有中雨",
      copy: current.live ? "户外与较长步行可能受影响。" : "江边与较长步行可能受影响。",
      actions: [{ action: "preview", label: "查看方案", ariaLabel: "查看雨天方案" }],
    };
  }

  function groupPlanDays(plan) {
    const stops = Array.isArray(plan?.stops) ? plan.stops : [];
    const indexes = stops.map((item) => Number(item.day_index) || 1);
    const dayCount = Math.max(
      1,
      Number(plan?.day_count) || 1,
      indexes.length ? Math.max(...indexes) : 1,
    );
    const days = [];
    for (let dayIndex = 1; dayIndex <= dayCount; dayIndex += 1) {
      const dayStops = stops
        .filter((item) => (Number(item.day_index) || 1) === dayIndex)
        .map((item) => ({
          poiId: item.poi_id || "",
          name: item.name || "",
          time: item.arrival_time || "",
          district: item.district || "",
        }));
      days.push({
        dayIndex,
        label: `D${dayIndex}`,
        stops: dayStops,
        summary: dayStops.length
          ? dayStops
              .map((stop) => (stop.time ? `${stop.time} ${stop.name}` : stop.name))
              .join(" · ")
          : "还没排上具体站点",
      });
    }
    return days;
  }

  function stopKey(stop) {
    return String(stop?.poi_id || stop?.name || "");
  }

  function diffRoutePlans(previousPlan, nextPlan) {
    const previous = Array.isArray(previousPlan?.stops) ? previousPlan.stops : [];
    const next = Array.isArray(nextPlan?.stops) ? nextPlan.stops : [];
    const prevKeys = new Set(previous.map(stopKey));
    const prevSig = previous
      .map((stop) => `${Number(stop.day_index) || 1}:${stopKey(stop)}`)
      .join("|");
    const nextSig = next
      .map((stop) => `${Number(stop.day_index) || 1}:${stopKey(stop)}`)
      .join("|");
    const added = next.filter((stop) => stopKey(stop) && !prevKeys.has(stopKey(stop)));
    const lastByDay = {};
    next.forEach((stop) => {
      lastByDay[Number(stop.day_index) || 1] = stop;
    });
    return {
      changed: Boolean(previousPlan) && prevSig !== nextSig,
      addedNames: added.map((stop) => stop.name).filter(Boolean),
      lastStops: lastByDay,
    };
  }

  function routeCardPresentation(input) {
    const plan = input?.plan || null;
    const previousPlan = input?.previousPlan || null;
    const needsClarification = Boolean(input?.needsClarification);
    const preferNear = String(input?.preferNear || "").trim();
    const days = groupPlanDays(plan);
    const compact = days
      .map((day) => {
        const names = day.stops.map((stop) => stop.name).filter(Boolean).slice(0, 3);
        return names.length ? `${day.label} ${names.join("、")}` : `${day.label} 待补`;
      })
      .join(" · ");
    const diff = previousPlan
      ? diffRoutePlans(previousPlan, plan)
      : { changed: false, addedNames: [], lastStops: {} };
    const lastDayOne = diff.lastStops[1] || (days[0]?.stops || []).slice(-1)[0] || null;
    const lastName = lastDayOne?.name || "";

    if (needsClarification) {
      return {
        mode: "clarify",
        kicker: "路线先不动",
        title: "这一版还在，我先问清楚再改",
        copy: "告诉我大概在哪个区域、几点开始，我再把前后安排就近收一收。",
        bannerTitle: "上一版路线还在",
        bannerCopy: compact || "先看着这版，等你补一句我再改。",
        days,
        highlightNames: [],
        changeNote: "",
      };
    }
    if (previousPlan && diff.changed) {
      const lastBit = lastName
        ? preferNear
          ? `最后一站到${lastName}，赴约会近一些。`
          : `这一天收到${lastName}。`
        : "";
      return {
        mode: "updated",
        kicker: "已按你说的更新",
        title: preferNear ? `行程已就近收到${preferNear}` : "路线已经按你的话改了一版",
        copy: preferNear
          ? `已按你说的更新，白天想看的还在，末段往${preferNear}收。${lastBit}`
          : `已按你说的更新，想看的地方还在，只动了相关的一段。${lastBit}`,
        bannerTitle: "路线已经按你的话更新",
        bannerCopy: compact,
        days,
        highlightNames: lastName ? [lastName] : [],
        changeNote: preferNear ? `末站靠近你的安排 · ${preferNear}` : "已按你说的更新",
      };
    }
    return {
      mode: "draft",
      kicker: "行程草稿",
      title: days.length > 1 ? `${days.length} 天路线已经有一版` : "路线已经有一版啦",
      copy: compact || "先看看这几天怎么走。",
      bannerTitle: days.length > 1 ? `${days.length} 天路线已经有一版` : "路线已经有一版啦",
      bannerCopy: compact,
      days,
      highlightNames: [],
      changeNote: "",
    };
  }

  return {
    countRecordedJourneys,
    filterTripsByCity,
    resolveLaunchState,
    summarizeTripContext,
    resolveWeatherDemo,
    weatherNoticeFromPlan,
    transitionWeatherDemo,
    weatherDemoPresentation,
    tripContextReady,
    groupPlanDays,
    diffRoutePlans,
    routeCardPresentation,
  };
});
