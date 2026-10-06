export async function api<T>(
  path: string,
  body?: unknown,
  token?: string,
): Promise<T> {
  const response = await fetch("/api" + path, {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined
        ? {}
        : {
            "Content-Type": "application/json",
            "X-Explorer-Token": token || "",
          },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const content = await response.json().catch(() => ({
    detail: "The local server is unavailable. Check that it is still running.",
  }));
  if (!response.ok)
    throw new Error(
      typeof content.detail === "string"
        ? content.detail
        : "The request could not be completed.",
    );
  return content as T;
}
