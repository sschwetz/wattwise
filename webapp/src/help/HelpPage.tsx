import { useEffect, useMemo } from "react";
import Markdown from "react-markdown";
import { Link, useLocation, useParams } from "react-router-dom";
import rehypeSlug from "rehype-slug";
import remarkGfm from "remark-gfm";
import { getChapter, INDEX_SLUG } from "./content";
import { createMarkdownComponents } from "./markdownComponents";
import { linkClass } from "./styles";

const remarkPlugins = [remarkGfm];
const rehypePlugins = [rehypeSlug];

/** After each navigation, scroll to the URL hash target, or to the top when there is none. */
function useScrollToHash() {
  const { hash, key } = useLocation();
  useEffect(() => {
    const id = hash ? decodeURIComponent(hash.slice(1)) : "";
    const target = id ? document.getElementById(id) : null;
    if (target) target.scrollIntoView();
    else window.scrollTo(0, 0);
  }, [hash, key]);
}

/**
 * The in-app user guide. Mounted at /sites/:siteId/help/:slug? (inside the
 * site sidebar) and at /help/:slug? (header only, for users with no site).
 */
export function HelpPage() {
  const { siteId, slug = INDEX_SLUG } = useParams<{ siteId?: string; slug?: string }>();
  const base = siteId ? `/sites/${siteId}/help` : "/help";
  const markdown = getChapter(slug);
  const components = useMemo(() => createMarkdownComponents({ base, currentSlug: slug }), [base, slug]);
  useScrollToHash();

  if (markdown === undefined) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-8">
        <h1 className="mb-4 font-display text-3xl font-bold text-ink">Page not found</h1>
        <p className="text-ink-muted">
          There is no help page called &quot;{slug}&quot;.{" "}
          <Link to={base} className={linkClass}>
            Back to the help contents
          </Link>
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <article>
        <Markdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={components}>
          {markdown}
        </Markdown>
      </article>
    </div>
  );
}
