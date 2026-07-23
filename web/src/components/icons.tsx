import type { SVGProps } from "react";

const Icon = ({ children, ...props }: SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" {...props}>
    {children}
  </svg>
);

export const PolyGateMark = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M3 12h5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    <rect x="8" y="8" width="8" height="8" rx="2.7" fill="var(--accent-soft)" stroke="currentColor" strokeWidth="1.5" />
    <path d="m10.5 9.8 2.8 2.2-2.8 2.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    <path className="polygate-branch-outer" d="M16 12h.7A2.3 2.3 0 0 0 19 9.7V6h2M16 12h.7a2.3 2.3 0 0 1 2.3 2.3V18h2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M16 12h5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    <circle cx="3" cy="12" r="1.15" fill="currentColor" />
    <circle className="polygate-endpoint-outer" cx="21" cy="6" r="1.15" fill="currentColor" />
    <circle cx="21" cy="12" r="1.15" fill="currentColor" />
    <circle className="polygate-endpoint-outer" cx="21" cy="18" r="1.15" fill="currentColor" />
  </Icon>
);

export const PlusIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}><path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></Icon>
);

export const SendIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}><path d="m4 4 16 8-16 8 3-8-3-8Zm3 8h13" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></Icon>
);

export const MenuIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}><path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></Icon>
);

export const SidebarIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <rect x="3.5" y="4" width="17" height="16" rx="2.5" stroke="currentColor" strokeWidth="1.5" />
    <path d="M9 4v16M15 9l-3 3 3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
  </Icon>
);

export const CloseIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}><path d="m6 6 12 12M18 6 6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></Icon>
);

export const RouteIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}><path d="M5 4v4a4 4 0 0 0 4 4h10M5 20v-4a4 4 0 0 1 4-4M16 9l3 3-3 3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></Icon>
);

export const CopyIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}><rect x="8" y="8" width="11" height="11" rx="2" stroke="currentColor" strokeWidth="1.7" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" stroke="currentColor" strokeWidth="1.7" /></Icon>
);

export const RefreshIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M19 7v5h-5M5 17v-5h5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M7.1 8.1A6.5 6.5 0 0 1 18.6 11M5.4 13A6.5 6.5 0 0 0 16.9 15.9" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
  </Icon>
);

export const ChevronIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}><path d="m7 9.5 5 5 5-5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></Icon>
);

export const MoreIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}><circle cx="5" cy="12" r="1.5" fill="currentColor" /><circle cx="12" cy="12" r="1.5" fill="currentColor" /><circle cx="19" cy="12" r="1.5" fill="currentColor" /></Icon>
);
