# Frontend Agent Notes

<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version may have breaking changes in APIs, conventions, and file structure. Read the relevant guide in `node_modules/next/dist/docs/` before writing code that depends on framework-specific behavior. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

## Project-Specific Rules

- Do not call model services from the frontend. Use backend APIs through `NEXT_PUBLIC_API_URL`.
- Keep User, Admin, and Login visual language consistent: white surfaces, slate text, blue/indigo accent, subtle borders, compact cards.
- Preserve user processing behavior when editing UI. Upload, detect, ROI, OCR, Ground Truth, export, and Processing Logs must remain compatible.
- System Maintenance is backend source of truth. UI may display state and reset workspace, but processing enforcement must remain backend-side.
- Verification labels shown to users:
  - `standard` -> `การตรวจสอบแบบถ่วงน้ำหนักคะแนน`
  - `strict` -> `การตรวจสอบแบบลำดับเงื่อนไข`
- Internal API values remain `standard` and `strict`.
- Do not reintroduce frontend mocks for Processing Logs or Admin Dashboard data.
- When rendering authenticated images, use authenticated fetch to blob/object URL and clean up object URLs.
