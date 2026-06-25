import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error("[ErrorBoundary]", error, errorInfo);
  }

  handleRefresh = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="error-boundary-fallback" role="alert">
          <div className="error-boundary-content">
            <h2>出错了，请刷新页面</h2>
            <p>应用遇到了一个意料之外的错误，请尝试刷新页面后继续操作。</p>
            <button type="button" className="error-boundary-refresh" onClick={this.handleRefresh}>
              刷新页面
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
