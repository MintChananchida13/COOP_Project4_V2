import AdminTemplateEditPage from "../../../../../admin/AdminTemplateEditPage";

export default async function TemplateEditRoute({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AdminTemplateEditPage templateId={id} />;
}
