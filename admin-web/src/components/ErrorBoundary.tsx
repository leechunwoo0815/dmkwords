import { Component, type ReactNode } from "react";

// R1（插修 15）：渲染异常兜底——E-20260831-04 白屏欠账（无 ErrorBoundary 时
// 单点渲染错误=React 整树卸载全站白屏）。捕获后显示兜底 UI + console.error
// 原始堆栈（供 devtools 排查），刷新可恢复。
type Props = { children: ReactNode };
type State = { error: Error | null };

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: unknown) {
    // 原始堆栈进 console（不吞）——兜底 UI 只管不让用户看到白屏
    console.error("[ErrorBoundary] 渲染异常：", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div
          style={{
            display: "flex", flexDirection: "column", alignItems: "center",
            justifyContent: "center", height: "100vh", gap: 12,
          }}
        >
          <div style={{ fontSize: 40 }}>🎨</div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>页面出错了</div>
          <div style={{ color: "rgba(0,0,0,0.45)", fontSize: 13 }}>
            请刷新重试；若持续出现请联系管理员
          </div>
          <button
            onClick={() => window.location.reload()}
            style={{
              marginTop: 8, padding: "6px 20px", borderRadius: 6,
              border: "1px solid #d9d9d9", cursor: "pointer", background: "#fff",
            }}
          >
            刷新页面
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
