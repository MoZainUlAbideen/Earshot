import Link from "next/link";

/** Four red bars: a tiny sound wave. The only red on the page besides the main button. */
export function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2.5 font-display text-xl font-bold tracking-wide">
      <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true" className="fill-brand">
        <rect x="1" y="8" width="3" height="6" rx="1" />
        <rect x="6" y="4" width="3" height="14" rx="1" />
        <rect x="11" y="1" width="3" height="20" rx="1" />
        <rect x="16" y="6" width="3" height="10" rx="1" />
      </svg>
      Earshot
    </Link>
  );
}
