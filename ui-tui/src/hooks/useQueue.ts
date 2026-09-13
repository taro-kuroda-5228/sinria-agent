import { useCallback, useRef, useState } from 'react'

// Mutates `arr` in place; returned reference is the same input array, kept
// so callers can chain. Use `Array.prototype.toSpliced` if you need a copy.
export function removeAtInPlace<T>(arr: T[], i: number): T[] {
  if (i < 0 || i >= arr.length) {
    return arr
  }

  arr.splice(i, 1)

  return arr
}

export interface QueuedSubmission {
  autonomyIngressText?: string
  displayKind?: string
  text: string
}

export function shiftQueuedSubmission(
  queue: string[],
  displayKinds: Array<string | undefined>,
  autonomyIngresses: Array<string | undefined>
): QueuedSubmission | undefined {
  const text = queue.shift()
  const displayKind = displayKinds.shift()
  const autonomyIngressText = autonomyIngresses.shift()

  return text === undefined ? undefined : { autonomyIngressText, displayKind, text }
}

export function useQueue() {
  const queueRef = useRef<string[]>([])
  const queueDisplayKindRef = useRef<Array<string | undefined>>([])
  const queueAutonomyIngressRef = useRef<Array<string | undefined>>([])
  const [queuedDisplay, setQueuedDisplay] = useState<string[]>([])
  const queueEditRef = useRef<number | null>(null)
  const [queueEditIdx, setQueueEditIdx] = useState<number | null>(null)

  const syncQueue = useCallback(() => setQueuedDisplay([...queueRef.current]), [])

  const setQueueEdit = useCallback((idx: number | null) => {
    queueEditRef.current = idx
    setQueueEditIdx(idx)
  }, [])

  const enqueue = useCallback(
    (text: string, displayKind?: string, autonomyIngressText?: string) => {
      queueRef.current.push(text)
      queueDisplayKindRef.current.push(displayKind)
      queueAutonomyIngressRef.current.push(autonomyIngressText)
      syncQueue()
    },
    [syncQueue]
  )

  const dequeue = useCallback(() => {
    const head = shiftQueuedSubmission(
      queueRef.current,
      queueDisplayKindRef.current,
      queueAutonomyIngressRef.current
    )

    syncQueue()

    return head?.text
  }, [syncQueue])

  const dequeueWithMetadata = useCallback(() => {
    const head = shiftQueuedSubmission(
      queueRef.current,
      queueDisplayKindRef.current,
      queueAutonomyIngressRef.current
    )

    syncQueue()

    return head
  }, [syncQueue])

  const replaceQ = useCallback(
    (i: number, text: string) => {
      queueRef.current[i] = text
      queueDisplayKindRef.current[i] = undefined
      queueAutonomyIngressRef.current[i] = undefined
      syncQueue()
    },
    [syncQueue]
  )

  const removeQ = useCallback(
    (i: number) => {
      const before = queueRef.current.length

      removeAtInPlace(queueRef.current, i)
      removeAtInPlace(queueDisplayKindRef.current, i)
      removeAtInPlace(queueAutonomyIngressRef.current, i)

      if (queueRef.current.length !== before) {
        syncQueue()
      }
    },
    [syncQueue]
  )

  return {
    dequeue,
    dequeueWithMetadata,
    enqueue,
    queueEditIdx,
    queueEditRef,
    queueAutonomyIngressRef,
    queueDisplayKindRef,
    queueRef,
    queuedDisplay,
    removeQ,
    replaceQ,
    setQueueEdit,
    syncQueue
  }
}
