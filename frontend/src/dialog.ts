import type { MouseEvent, PointerEvent } from 'react'

// a popup (a modal dialog) closes on a click outside its box, as on Close and Escape: the click on the backdrop lands
// on the dialog itself, outside its box. Only when the press started outside too: a text selection that starts inside
// and ends on the backdrop lands its click on the dialog as well. Spread on the dialog: <dialog {...closeOutside}>
const outside = (e: MouseEvent<HTMLDialogElement>) => {
  if (e.target !== e.currentTarget) return false
  const box = e.currentTarget.getBoundingClientRect()
  return e.clientX < box.left || e.clientX > box.right || e.clientY < box.top || e.clientY > box.bottom
}
const pressed = new WeakSet<HTMLDialogElement>()

export const closeOutside = {
  onPointerDown: (e: PointerEvent<HTMLDialogElement>) => {
    if (outside(e)) pressed.add(e.currentTarget)
    else pressed.delete(e.currentTarget)
  },
  onClick: (e: MouseEvent<HTMLDialogElement>) => {
    if (pressed.has(e.currentTarget) && outside(e)) e.currentTarget.close()
    pressed.delete(e.currentTarget)
  },
}
