"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { MapPin, Search } from "lucide-react"
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { normalizeVi } from "@/lib/normalize"
import { fetchSearch, fuseSearch, type SearchResult } from "@/lib/search"

interface SearchSectionProps {
  onSelectWard: (ward: SearchResult) => void
  onDropdownOpenChange: (open: boolean) => void
  onSearchStart?: () => void
  variant?: "top" | "hero"
}

const DEBOUNCE_MS = 300

export function SearchSection({
  onSelectWard,
  onDropdownOpenChange,
  onSearchStart,
  variant = "top",
}: SearchSectionProps) {
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<SearchResult[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [isOpen, setIsOpen] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    onDropdownOpenChange(isOpen)
  }, [isOpen, onDropdownOpenChange])

  const runSearch = useCallback(async (q: string) => {
    const normalized = normalizeVi(q)
    if (!normalized) {
      setResults([])
      setIsOpen(false)
      return
    }

    const local = fuseSearch(normalized)
    if (local.length > 0) {
      setResults(local)
      setIsOpen(true)
    }

    abortRef.current?.abort()
    abortRef.current = new AbortController()
    setIsLoading(true)

    try {
      const remote = await fetchSearch(q, abortRef.current.signal)
      setResults(remote)
      setIsOpen(remote.length > 0)
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)

    if (!query.trim()) {
      setResults([])
      setIsOpen(false)
      setIsLoading(false)
      return
    }

    onSearchStart?.()

    debounceRef.current = setTimeout(() => {
      runSearch(query)
    }, DEBOUNCE_MS)

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [query, runSearch])

  function handleSelect(ward: SearchResult) {
    setQuery("")
    setResults([])
    setIsOpen(false)
    onSelectWard(ward)
  }

  const dropdown = isOpen && (
    <div
      className={
        variant === "hero"
          ? "absolute left-0 top-full z-30 mt-2 w-full rounded-xl border border-slate-100 bg-white shadow-xl"
          : "absolute left-0 top-full z-30 w-full border-t border-slate-100 bg-white shadow-lg"
      }
    >
      <CommandList>
        {!isLoading && results.length === 0 && (
          <CommandEmpty data-testid="search-empty">
            Không tìm thấy phường nào
          </CommandEmpty>
        )}

        {isLoading && results.length === 0 && (
          <div className="space-y-1 p-2">
            {[...Array(3)].map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        )}

        {results.map((ward) => (
          <CommandItem
            key={ward.ward_normalized}
            value={ward.ward_normalized}
            onSelect={() => handleSelect(ward)}
            data-testid="search-result-item"
          >
            <MapPin className="h-3.5 w-3.5 shrink-0 text-slate-400" />
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="truncate font-medium text-slate-900">{ward.location_name}</span>
              <span className="truncate text-xs text-slate-500">
                {ward.location_type !== "ward" ? `${ward.ward} · ` : ""}
                {ward.district && ward.district !== "Unknown" ? ward.district : "Hà Nội"}
              </span>
            </div>
            {ward.has_flood_data ? (
              <Badge variant="has-data" data-testid="badge-has-data">✓ Có dữ liệu</Badge>
            ) : (
              <Badge variant="no-data" data-testid="badge-no-data">— Chưa có</Badge>
            )}
          </CommandItem>
        ))}
      </CommandList>
    </div>
  )

  if (variant === "hero") {
    return (
      <div className="relative w-full">
        <div className="flex items-center gap-0 rounded-full bg-white py-1.5 pl-4 pr-1.5 shadow-2xl shadow-black/40">
          <Search className="h-4 w-4 shrink-0 text-slate-400" />
          <Command
            shouldFilter={false}
            className="hero-search static flex-1 bg-transparent"
          >
            <CommandInput
              placeholder="Tìm phường, ngõ... (VD: Mỹ Đình, Cầu Giấy)"
              value={query}
              onValueChange={setQuery}
              onFocus={() => {
                if (results.length > 0) setIsOpen(true)
              }}
              onBlur={() => {
                setTimeout(() => setIsOpen(false), 150)
              }}
              data-testid="search-input"
            />
            {isLoading && (
              <div className="h-0.5 w-full overflow-hidden bg-slate-100">
                <div className="h-full animate-pulse bg-sky-500" />
              </div>
            )}
            {dropdown}
          </Command>
          <button
            type="button"
            className="shrink-0 rounded-full bg-sky-700 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-sky-600"
            onClick={() => {
              if (results.length > 0) handleSelect(results[0])
            }}
          >
            Tra cứu
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="relative w-full border-b border-slate-200 bg-white shadow-sm">
      <Command shouldFilter={false} className="w-full">
        <CommandInput
          placeholder="Tìm phường, xã... (VD: Mỹ Đình, Cầu Giấy)"
          value={query}
          onValueChange={setQuery}
          onFocus={() => {
            if (results.length > 0) setIsOpen(true)
          }}
          onBlur={() => {
            setTimeout(() => setIsOpen(false), 150)
          }}
          data-testid="search-input"
        />

        {isLoading && (
          <div className="h-0.5 w-full overflow-hidden bg-slate-100">
            <div className="h-full animate-pulse bg-blue-500" />
          </div>
        )}

        {dropdown}
      </Command>
    </div>
  )
}
