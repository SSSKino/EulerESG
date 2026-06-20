import { AUTH_CARD_HEIGHT, AUTH_CARD_WIDTH } from "./authLayout";

type AuthCardProps = {
  children: React.ReactNode;
};

export default function AuthCard({ children }: AuthCardProps) {
  return (
    <div
      className={`flex ${AUTH_CARD_WIDTH} ${AUTH_CARD_HEIGHT} flex-col rounded-[1.75rem] bg-white/76 p-10 shadow-[0_24px_60px_rgba(0,0,0,0.10)] backdrop-blur-[1px] md:p-12`}
    >
      {children}
    </div>
  );
}
