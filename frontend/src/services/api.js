import axios from "axios";

const configuredBaseURL = import.meta.env.VITE_API_BASE_URL;

const API_BASE_URL =
  typeof configuredBaseURL === "string" && configuredBaseURL.trim()
    ? configuredBaseURL.trim().replace(/\/+$/, "")
    : "http://127.0.0.1:5000/api";

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  // Authentication uses secure, HttpOnly cookies issued by Flask.
  withCredentials: true,
  headers: {
    Accept: "application/json",
    "X-Requested-With": "XmlHttpRequest",
  },
});

let csrfToken = null;
let csrfRequest = null;

async function ensureCsrfToken() {
  if (csrfToken) return csrfToken;

  if (!csrfRequest) {
    csrfRequest = api
      .get("/auth/csrf")
      .then((response) => {
        const token = response?.data?.csrf_token;
        if (typeof token !== "string" || token.length < 32) {
          throw new Error("The server did not issue a valid CSRF token.");
        }
        csrfToken = token;
        return csrfToken;
      })
      .finally(() => {
        csrfRequest = null;
      });
  }

  return csrfRequest;
}

api.interceptors.request.use(async (config) => {
  const method = String(config.method || "get").toLowerCase();
  const unsafeMethod = ["post", "put", "patch", "delete"].includes(method);

  if (!unsafeMethod) return config;

  const token = await ensureCsrfToken();
  config.headers = config.headers || {};

  if (typeof config.headers.set === "function") {
    config.headers.set("X-CSRF-Token", token);
  } else {
    config.headers["X-CSRF-Token"] = token;
  }

  return config;
});

api.interceptors.response.use(
  (response) => {
    const nextToken = response?.data?.csrf_token;
    if (typeof nextToken === "string" && nextToken.length >= 32) {
      csrfToken = nextToken;
    }

    if (String(response?.config?.url || "").endsWith("/auth/logout")) {
      csrfToken = null;
    }

    return response;
  },
  async (error) => {
    const originalRequest = error?.config;
    const isCsrfFailure =
      error?.response?.status === 403 &&
      error?.response?.data?.error === "csrf_failed";

    if (isCsrfFailure && originalRequest && !originalRequest._csrfRetry) {
      originalRequest._csrfRetry = true;
      csrfToken = null;
      csrfRequest = null;

      try {
        const token = await ensureCsrfToken();
        originalRequest.headers = originalRequest.headers || {};
        if (typeof originalRequest.headers.set === "function") {
          originalRequest.headers.set("X-CSRF-Token", token);
        } else {
          originalRequest.headers["X-CSRF-Token"] = token;
        }
        return api.request(originalRequest);
      } catch {
        // Preserve the original API error if token refresh also fails.
      }
    }

    return Promise.reject(error);
  },
);

export function getApiErrorMessage(
  error,
  fallback = "Something went wrong. Please try again."
) {
  if (error?.response?.data) {
    const payload = error.response.data;

    if (typeof payload.message === "string" && payload.message.trim()) {
      return payload.message;
    }

    if (typeof payload.error === "string" && payload.error.trim()) {
      return payload.error;
    }

    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail;
    }
  }

  if (error?.code === "ERR_CANCELED") {
    return "The request was cancelled.";
  }

  if (error?.code === "ECONNABORTED" || error?.code === "ETIMEDOUT") {
    return "The server took too long to respond. Please try again.";
  }

  if (error?.code === "ERR_NETWORK") {
    return "Cannot reach the IDP server. Check that the Flask backend is running.";
  }

  return fallback;
}

export default api;
