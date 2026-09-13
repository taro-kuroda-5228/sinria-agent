import { describe, expect, it } from 'vitest'

import { removeAtInPlace, shiftQueuedSubmission } from '../hooks/useQueue.js'

describe('removeAtInPlace', () => {
  it('removes the item at the given index in place', () => {
    const arr = ['a', 'b', 'c']

    removeAtInPlace(arr, 1)
    expect(arr).toEqual(['a', 'c'])
  })

  it('is a no-op when the index is out of bounds', () => {
    const arr = ['a', 'b']

    removeAtInPlace(arr, -1)
    removeAtInPlace(arr, 5)
    expect(arr).toEqual(['a', 'b'])
  })

  it('returns the same reference (mutates in place)', () => {
    const arr = ['x']
    const same = removeAtInPlace(arr, 0)

    expect(same).toBe(arr)
    expect(arr).toEqual([])
  })
})

describe('shiftQueuedSubmission', () => {
  it('preserves synthetic display metadata during automatic dequeue', () => {
    const queue = ['expanded skill payload']
    const displayKinds = ['command_dispatch']
    const autonomyIngresses = [undefined]

    expect(shiftQueuedSubmission(queue, displayKinds, autonomyIngresses)).toEqual({
      autonomyIngressText: undefined,
      displayKind: 'command_dispatch',
      text: 'expanded skill payload'
    })
    expect(queue).toEqual([])
    expect(displayKinds).toEqual([])
    expect(autonomyIngresses).toEqual([])
  })

  it('preserves immutable direct ingress separately from transformed text', () => {
    const queue = ['expanded file-derived payload']
    const displayKinds = [undefined]

    const autonomyIngresses = [
      'Continue autonomously until all remaining tasks are complete. [[paste:1]]'
    ]

    expect(shiftQueuedSubmission(queue, displayKinds, autonomyIngresses)).toEqual({
      autonomyIngressText:
        'Continue autonomously until all remaining tasks are complete. [[paste:1]]',
      displayKind: undefined,
      text: 'expanded file-derived payload'
    })
  })
})
