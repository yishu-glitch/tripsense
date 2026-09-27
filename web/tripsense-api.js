(function (root, factory) {
  const exported = factory();
  if (typeof window === "object" && window.document) {
    window.TripSenseApi = exported.TripSenseApi;
  } else if (typeof module === "object" && module.exports) {
    module.exports = exported;
  } else {
    root.TripSenseApi = exported.TripSenseApi;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  class TripSenseApi {
    constructor({
      fetchImpl = typeof globalThis.fetch === "function"
        ? globalThis.fetch.bind(globalThis)
        : null,
      xhrFactory = typeof XMLHttpRequest === "function"
        ? () => new XMLHttpRequest()
        : null,
      baseUrl = "",
    } = {}) {
      if (typeof fetchImpl !== "function" && typeof xhrFactory !== "function") {
        throw new Error("no browser HTTP transport is available");
      }
      this.fetchImpl = fetchImpl;
      this.xhrFactory = xhrFactory;
      this.baseUrl = baseUrl.replace(/\/$/, "");
    }

    async request(path, { method = "GET", body } = {}) {
      if (typeof this.fetchImpl !== "function") {
        return this.requestWithXhr(path, { method, body });
      }
      const options = { method, headers: { Accept: "application/json" } };
      if (body !== undefined) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
      }
      const response = await this.fetchImpl(this.baseUrl + path, options);
      let data = null;
      try {
        data = await response.json();
      } catch (_error) {
        data = {};
      }
      if (!response.ok) {
        const error = new Error(data.detail || `TripSense API error (${response.status})`);
        error.status = response.status;
        error.payload = data;
        throw error;
      }
      return data;
    }

    requestWithXhr(path, { method = "GET", body } = {}) {
      return new Promise((resolve, reject) => {
        const xhr = this.xhrFactory();
        xhr.open(method, this.baseUrl + path);
        xhr.setRequestHeader("Accept", "application/json");
        if (body !== undefined) xhr.setRequestHeader("Content-Type", "application/json");
        xhr.onload = () => {
          let data = {};
          try {
            data = JSON.parse(xhr.responseText || "{}");
          } catch (_error) {
            data = {};
          }
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve(data);
            return;
          }
          const error = new Error(data.detail || `TripSense API error (${xhr.status})`);
          error.status = xhr.status;
          error.payload = data;
          reject(error);
        };
        xhr.onerror = () => reject(new Error("TripSense API network error"));
        xhr.send(body === undefined ? null : JSON.stringify(body));
      });
    }

    health() {
      return this.request("/api/v1/health");
    }

    chat(payload) {
      return this.request("/api/v1/chat/respond", { method: "POST", body: payload });
    }

    listJourneys() {
      return this.request("/api/v1/journeys");
    }

    saveJourney(payload) {
      return this.request("/api/v1/journeys", { method: "POST", body: payload });
    }

    addRecord(journeyId, payload) {
      return this.request(`/api/v1/journeys/${journeyId}/records`, {
        method: "POST",
        body: payload,
      });
    }

    listRecords(journeyId) {
      return this.request(`/api/v1/journeys/${journeyId}/records`);
    }

    generateDiary(journeyId) {
      return this.request(`/api/v1/journeys/${journeyId}/diaries/generate`, {
        method: "POST",
      });
    }

    latestDiary(journeyId) {
      return this.request(`/api/v1/journeys/${journeyId}/diaries/latest`);
    }
  }

  return { TripSenseApi };
});
