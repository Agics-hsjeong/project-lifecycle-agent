import React, { Component, type ReactNode } from "react";
/** Unexpected view errors remain recoverable and never leave a blank screen. */
export class WorkspaceBoundary extends Component<
  { children: ReactNode },
  { failed: boolean; attempt: number }
> {
  state = { failed: false, attempt: 0 };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed)
      return (
        <main
          className="ieum-workspace theme-light"
          style={{ display: "block", padding: 32 }}
        >
          <section role="alert">
            <h1>관제 화면을 다시 표시해야 합니다.</h1>
            <p>
              프로젝트 원본은 변경되지 않았습니다. 화면을 다시 열거나 올바른
              스냅샷으로 다시 연결해 주세요.
            </p>
            <button
              onClick={() =>
                this.setState(({ attempt }) => ({
                  failed: false,
                  attempt: attempt + 1,
                }))
              }
            >
              화면 다시 열기
            </button>
          </section>
        </main>
      );
    return (
      <React.Fragment key={this.state.attempt}>
        {this.props.children}
      </React.Fragment>
    );
  }
}
