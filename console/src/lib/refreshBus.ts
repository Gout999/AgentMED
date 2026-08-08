/** 全局手动刷新事件总线：顶栏刷新按钮 → 所有已挂载页面的 usePageData 重载。 */

type Listener = () => void;

const listeners = new Set<Listener>();

export function subscribeRefresh(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function emitRefresh(): void {
  listeners.forEach((fn) => fn());
}
