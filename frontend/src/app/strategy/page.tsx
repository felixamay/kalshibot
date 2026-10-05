"use client";

import { useEffect, useState } from "react";
import { StrategySettings } from "@/components/StrategySettings";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function StrategyPage() {
  const [token, setToken] = useState<string | null>(null);
  useEffect(() => {
    setToken(localStorage.getItem("kt_token"));
  }, []);
  return (
    <main className="min-h-screen px-4 pb-16 pt-6 md:px-8 max-w-6xl mx-auto">
      <p className="font-mono text-[11px] uppercase tracking-[0.25em] text-signal-mint">Your Kalshi strategy</p>
      <h1 className="font-display text-5xl mt-1">Strategy</h1>
      <StrategySettings apiUrl={API_URL} token={token} />
    </main>
  );
}
