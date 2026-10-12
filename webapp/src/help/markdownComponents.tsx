import type { ComponentPropsWithoutRef, JSX } from "react";
import type { Components } from "react-markdown";
import { Link } from "react-router-dom";
import { getImageUrl } from "./content";
import { chapterPath, classifyLink } from "./links";
import { linkClass } from "./styles";

interface LinkContext {
  /** Help route prefix: "/help" or "/sites/:id/help". */
  base: string;
  currentSlug: string;
}

const warned = new Set<string>();
function warnUnknownLink(href: string) {
  if (!import.meta.env.DEV || warned.has(href)) return;
  warned.add(href);
  console.warn(`Help: link to unknown chapter "${href}"`);
}

type El<T extends keyof JSX.IntrinsicElements> = Omit<ComponentPropsWithoutRef<T>, "ref"> & {
  node?: unknown;
};

/** Drops react-markdown's `node` prop so it does not reach the DOM. */
function dom<T extends { node?: unknown }>({ node: _node, ...rest }: T): Omit<T, "node"> {
  return rest;
}

const headingBase = "font-display text-ink scroll-mt-4";

export function createMarkdownComponents({ base, currentSlug }: LinkContext): Components {
  return {
    h1: (props: El<"h1">) => <h1 {...dom(props)} className={`${headingBase} mb-6 text-3xl font-bold`} />,
    h2: (props: El<"h2">) => (
      <h2 {...dom(props)} className={`${headingBase} mb-3 mt-10 border-b border-line pb-2 text-2xl font-semibold`} />
    ),
    h3: (props: El<"h3">) => <h3 {...dom(props)} className={`${headingBase} mb-2 mt-8 text-xl font-semibold`} />,
    h4: (props: El<"h4">) => <h4 {...dom(props)} className={`${headingBase} mb-2 mt-6 text-lg font-semibold`} />,
    p: (props: El<"p">) => <p {...dom(props)} className="my-4 leading-relaxed text-ink" />,
    ul: (props: El<"ul">) => <ul {...dom(props)} className="my-4 list-disc space-y-1 pl-6 text-ink" />,
    ol: (props: El<"ol">) => <ol {...dom(props)} className="my-4 list-decimal space-y-1 pl-6 text-ink" />,
    li: (props: El<"li">) => <li {...dom(props)} className="leading-relaxed marker:text-ink-muted" />,
    strong: (props: El<"strong">) => <strong {...dom(props)} className="font-semibold text-ink" />,
    hr: () => <hr className="my-8 border-line" />,
    blockquote: (props: El<"blockquote">) => (
      <blockquote {...dom(props)} className="my-4 border-l-4 border-solar bg-surface px-4 py-1 text-ink-muted" />
    ),
    code: (props: El<"code">) => (
      <code {...dom(props)} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[0.9em] text-ink" />
    ),
    pre: (props: El<"pre">) => (
      <pre {...dom(props)} className="my-4 overflow-x-auto rounded-md bg-surface-2 p-3 font-mono text-sm text-ink" />
    ),
    table: (props: El<"table">) => (
      <div className="my-4 overflow-x-auto rounded-md border border-line">
        <table {...dom(props)} className="w-full border-collapse text-left text-sm" />
      </div>
    ),
    thead: (props: El<"thead">) => <thead {...dom(props)} className="bg-surface-2" />,
    th: (props: El<"th">) => <th {...dom(props)} className="border-b border-line px-3 py-2 font-semibold text-ink" />,
    td: (props: El<"td">) => <td {...dom(props)} className="border-t border-line px-3 py-2 align-top text-ink" />,
    img: ({ src, alt }: El<"img">) => {
      const url = typeof src === "string" ? getImageUrl(src) : undefined;
      if (!url) return <span className="text-ink-muted">{alt}</span>;
      return (
        <img
          src={url}
          alt={alt ?? ""}
          loading="lazy"
          className="my-4 h-auto max-w-full rounded-md border border-line"
        />
      );
    },
    a: ({ href, children }: El<"a">) => {
      if (!href) return <>{children}</>;
      const link = classifyLink(href, currentSlug);
      if (link.kind === "external") {
        return (
          <a href={link.href} target="_blank" rel="noopener noreferrer" className={linkClass}>
            {children}
          </a>
        );
      }
      if (link.kind === "unknown") {
        warnUnknownLink(href);
        return <>{children}</>;
      }
      return (
        <Link to={{ pathname: chapterPath(base, link.slug), hash: link.hash }} className={linkClass}>
          {children}
        </Link>
      );
    },
  };
}
