import type { ReactNode } from "react";

interface CardProps {
  title?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}

/** 通用卡片容器：浅色底 + 白卡 + 细边框。 */
export function Card({ title, extra, children, className, bodyClassName }: CardProps) {
  return (
    <section className={`rounded-xl border border-gray-200 bg-white shadow-sm ${className ?? ""}`}>
      {(title || extra) && (
        <header className="flex items-center justify-between gap-3 border-b border-gray-100 px-4 py-3">
          <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
          {extra && <div className="flex items-center gap-2">{extra}</div>}
        </header>
      )}
      <div className={bodyClassName ?? "p-4"}>{children}</div>
    </section>
  );
}
