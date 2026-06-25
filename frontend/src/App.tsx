import { useMemo } from "react";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { LoginPage } from "./pages/LoginPage";
import { ExtractionFieldsPage } from "./pages/ExtractionFieldsPage";
import { ExtractionPage } from "./pages/ExtractionPage";
import { ExtractionRecordsPage } from "./pages/ExtractionRecordsPage";
import {
  navigateToExtraction,
  navigateToExtractionFields,
  navigateToExtractionRecords,
} from "./lib/routes";
import { useApp } from "./lib/state";
import { clearApiKey, listFieldDefinitions, setApiKey } from "./lib/api";

export function App() {
  const { state, dispatch } = useApp();
  const { currentUser, route, isSidebarExpanded, isExtractionMenuOpen } = state;

  async function handleLogin(apiKey: string): Promise<boolean> {
    setApiKey(apiKey);
    try {
      await listFieldDefinitions();
      dispatch({ type: "LOGIN", username: "API 用户" });
      return true;
    } catch {
      clearApiKey();
      return false;
    }
  }

  function handleLogout() {
    dispatch({ type: "LOGOUT" });
  }

  function handleExtractionMenuClick() {
    if (!isSidebarExpanded) {
      dispatch({ type: "TOGGLE_SIDEBAR" });
      if (!isExtractionMenuOpen) dispatch({ type: "TOGGLE_EXTRACTION_MENU" });
      return;
    }
    dispatch({ type: "TOGGLE_EXTRACTION_MENU" });
  }

  const content = useMemo(() => {
    if (route.name === "extract") {
      return <ExtractionPage />;
    }
    if (route.name === "extractRecords") {
      return <ExtractionRecordsPage onCreateExtraction={navigateToExtraction} />;
    }
    if (route.name === "extractFields") {
      return <ExtractionFieldsPage />;
    }
    return <ExtractionPage />;
  }, [route]);

  if (!currentUser) {
    return <LoginPage onLogin={handleLogin} />;
  }

  return (
    <ErrorBoundary>
    <div className={isSidebarExpanded ? "oa-frame sidebar-expanded" : "oa-frame"}>
      <aside className="oa-sidebar" aria-label="主导航">
        <strong className="oa-sidebar-title">{isSidebarExpanded ? "合同智能提取" : "提取"}</strong>
        <nav className="oa-nav">
          <div className={isExtractionMenuOpen ? "oa-nav-group open" : "oa-nav-group"}>
            <button
              className={
                route.name === "extract" || route.name === "extractRecords" || route.name === "extractFields"
                  ? "active"
                  : ""
              }
              type="button"
              onClick={handleExtractionMenuClick}
              aria-expanded={isSidebarExpanded ? isExtractionMenuOpen : undefined}
            >
              <span className="oa-extract-icon" aria-hidden="true" />
              <span>{isSidebarExpanded ? "合同智能提取" : "提取"}</span>
              {isSidebarExpanded && <span className="oa-menu-chevron" aria-hidden="true" />}
            </button>
            {isSidebarExpanded && isExtractionMenuOpen && (
              <div className="oa-subnav" aria-label="合同智能提取菜单">
                <button className={route.name === "extract" ? "active" : ""} type="button" onClick={navigateToExtraction}>
                  <span className="oa-subnav-dot" aria-hidden="true" />
                  <span>合同提取</span>
                </button>
                <button
                  className={route.name === "extractRecords" ? "active" : ""}
                  type="button"
                  onClick={navigateToExtractionRecords}
                  aria-current={route.name === "extractRecords" ? "page" : undefined}
                >
                  <span className="oa-history-icon" aria-hidden="true" />
                  <span>提取记录</span>
                </button>
                <button
                  className={route.name === "extractFields" ? "active" : ""}
                  type="button"
                  onClick={navigateToExtractionFields}
                  aria-current={route.name === "extractFields" ? "page" : undefined}
                >
                  <span className="oa-field-icon" aria-hidden="true" />
                  <span>提取字段管理</span>
                </button>
              </div>
            )}
          </div>
        </nav>
        {isSidebarExpanded && (
          <div className="oa-user-panel" aria-label="当前用户">
            <span className="oa-user-avatar" aria-hidden="true">
              {currentUser.slice(0, 1).toUpperCase()}
            </span>
            <div>
              <small>当前用户</small>
              <strong>{currentUser}</strong>
            </div>
            <button type="button" onClick={handleLogout}>
              退出登录
            </button>
          </div>
        )}
        <button
          className="oa-sidebar-footer"
          type="button"
          onClick={() => dispatch({ type: "TOGGLE_SIDEBAR" })}
          aria-label={isSidebarExpanded ? "收起侧边栏" : "展开侧边栏"}
          aria-pressed={isSidebarExpanded}
        >
          <span aria-hidden="true" />
        </button>
      </aside>

      <div className="oa-main">
        <main className="app-shell">{content}</main>
      </div>
    </div>
    </ErrorBoundary>
  );
}
