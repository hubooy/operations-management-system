// Shared by the development Worker and regression tests; never imported by production.
/**
 * @param {typeof fetch} nativeFetch
 * @param {() => unknown[]} allowedOrigins
 * @returns {typeof fetch}
 */
export function createPreviewFetch(nativeFetch, allowedOrigins) {
    return async (input, init) => {
        const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
        if (!allowedOrigins().includes(url.origin)) {
            throw new Error("preview_external_request_blocked");
        }
        // workerd supports manual/follow, not redirect:error. Inspect before following.
        const response = await nativeFetch(input, { ...init, redirect: "manual" });
        if (response.status >= 300 && response.status < 400) {
            await response.body?.cancel();
            throw new Error("preview_redirect_blocked");
        }
        return response;
    };
}
