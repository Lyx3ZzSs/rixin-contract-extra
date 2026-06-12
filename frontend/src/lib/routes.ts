export type AppRoute =
  | { name: "extract" }
  | { name: "extractRecords" }
  | { name: "extractFields" };

export function readRoute(pathname = window.location.pathname): AppRoute {
  if (pathname === "/extract/records") {
    return { name: "extractRecords" };
  }
  if (pathname === "/extract/fields") {
    return { name: "extractFields" };
  }
  return { name: "extract" };
}

export function navigateTo(path: string): void {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function navigateToExtraction(): void {
  navigateTo("/extract");
}

export function navigateToExtractionRecords(): void {
  navigateTo("/extract/records");
}

export function navigateToExtractionFields(): void {
  navigateTo("/extract/fields");
}
