# OCR Template Management Frontend

Next.js + TypeScript frontend for the OCR Template Management system.

## Runtime Role

The frontend owns UI state, workspace interaction, Admin management screens, and presentation. It does not call model services directly. All model inference goes through the backend via `NEXT_PUBLIC_API_URL`.

## Environment

Create `project_frontend/.env.local`.

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Run Locally

```powershell
cd project_frontend
npm install
npm run dev
```

Open:

```text
http://localhost:3000
```

## Main Areas

- `src/app/page.tsx`: User workspace, upload, maintenance notification, detect-dev orchestration, OCR, export, Processing Log snapshot.
- `src/admin/AdminSettingsPage.tsx`: System Maintenance, OCR model settings, verification strategy.
- `src/admin/AdminTemplatesPage.tsx`: Template library, active/nonactive controls, pending template badges.
- `src/admin/AdminTemplateEditPage.tsx`: Template ROI editor and pending field operations.
- `src/admin/AdminProcessingLogsPage.tsx`: Processing log list.
- `src/admin/AdminProcessingLogDetailPage.tsx`: Log preview, ROI overlay, results, export, manage log.
- `src/admin/adminApi.ts`: Backend API mapping and presentation normalization.
- `src/user/components/GroundTruthEditorZone.tsx`: User review and Ground Truth editing.
- `src/shared/workspace/WorkspaceCustomEditor.tsx`: Shared document/ROI workspace.

## Current UX Contracts

Verification labels:

- `standard` displays as `แบบมาตรฐาน (standard)`
- `strict` displays as `แบบเข้มงวด (strict)`

System Maintenance:

- User notification bell shows scheduled/active maintenance.
- Active maintenance blocks processing through backend and resets the user workspace to Upload.
- After maintenance ends, the user starts from Upload again.
- Admin Settings controls enforcement, schedule, and message.

Template Library:

- Pending template changes display as pending badges.
- The library refetches after the scheduled maintenance end time instead of polling continuously.

Processing Log Detail:

- Uses real backend data, not mocks.
- Fetches authenticated page images as blobs for preview.
- ROI overlay scales against the rendered image bounds and keeps all fields visible, with selected field highlighted.
- Image fields display image results without Ground Truth text comparison.

## Checks

```powershell
npx tsc --noEmit --pretty false
npm run build
```
