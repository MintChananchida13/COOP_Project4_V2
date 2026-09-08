import AdminRequestDetailPage from "../../../../../admin/AdminRequestDetailPage";
import AdminTemplateEditPage from "../../../../../admin/AdminTemplateEditPage";

export default async function TemplateEditRoute({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ stage?: string }>;
}) {
  const { id } = await params;
  const { stage } = await searchParams;
  if (stage === "editor") {
    return <AdminTemplateEditPage templateId={id} />;
  }
  return <AdminRequestDetailPage templateId={id} />;
}
