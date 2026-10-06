import type { Metadata } from "next";
import { Barlow, Inter, JetBrains_Mono } from "next/font/google";
import Link from "next/link";
import { Logo } from "@/components/logo";
import { WakeServer } from "@/components/wake-server";
import "./globals.css";

// Self-hosted by next/font: no request goes to Google when someone visits.
const barlow = Barlow({ variable: "--font-barlow", subsets: ["latin"], weight: ["500", "600", "700"] });
const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });
const jetbrains = JetBrains_Mono({ variable: "--font-jetbrains", subsets: ["latin"], weight: ["500"] });

export const metadata: Metadata = {
  title: { default: "Earshot", template: "%s · Earshot" },
  description:
    "Ask a question across podcasts and get a short answer where every claim links to the exact second it was said in the original audio.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${barlow.variable} ${inter.variable} ${jetbrains.variable} antialiased`}>
      <body className="flex min-h-dvh flex-col font-sans text-[15px] leading-relaxed">
        <WakeServer />
        <header className="sticky top-0 z-20 border-b border-edge bg-bg/90 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3.5 sm:px-6">
            <Logo />
            <nav className="flex items-center gap-5 text-sm text-ink-2">
              <Link href="/#how" className="hidden hover:text-accent sm:inline">How it works</Link>
              <Link href="/accuracy" className="hover:text-accent">Accuracy</Link>
              <Link href="/ask" className="rounded bg-brand px-3.5 py-2 font-semibold text-white hover:bg-brand-hi">
                Ask a question
              </Link>
            </nav>
          </div>
        </header>
        <div className="flex-1">{children}</div>
        <footer className="border-t border-edge">
          <div className="mx-auto flex max-w-6xl flex-wrap justify-between gap-3 px-4 py-6 text-sm text-muted sm:px-6">
            <span>Earshot · audio belongs to its publishers and streams from their servers</span>
            <a href="https://github.com/MoZainUlAbideen/Earshot" className="hover:text-accent">Source on GitHub</a>
          </div>
        </footer>
      </body>
    </html>
  );
}
