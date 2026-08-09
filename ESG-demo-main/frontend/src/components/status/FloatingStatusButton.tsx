import React, { useState } from "react";
import { FloatButton } from "antd";
import { MonitorOutlined } from "@ant-design/icons";
import SystemStatusMonitor from "./SystemStatus";
import { useT } from "@/i18n/useT";

const FloatingStatusButton: React.FC = () => {
  const { t } = useT();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const isDevelopment = process.env.NODE_ENV === "development";

  const showModal = () => {
    setIsModalOpen(true);
  };

  const hideModal = () => {
    setIsModalOpen(false);
  };

  if (!isDevelopment) return null;

  return (
    <>
      <FloatButton
        icon={<MonitorOutlined />}
        tooltip={t("statusPanel.backendSystemStatus")}
        onClick={showModal}
        style={{
          right: 24,
          bottom: 72,
        }}
        type="primary"
      />
      <SystemStatusMonitor open={isModalOpen} onClose={hideModal} />
    </>
  );
};

export default FloatingStatusButton;
