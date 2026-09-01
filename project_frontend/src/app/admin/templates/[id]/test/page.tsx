import AdminTemplateTestPage from "../../../../../admin/AdminTemplateTestPage";

export default async function TemplateTestRoute({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AdminTemplateTestPage templateId={id} />;
}
