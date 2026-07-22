import type { SVGProps } from "react";

const Icon = ({ children, ...props }: SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" {...props}>
    {children}
  </svg>
);

export const SparkIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M12 2v20M2 12h20M5 5l14 14M19 5 5 19" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    <circle cx="12" cy="12" r="3.2" fill="var(--accent-soft)" stroke="currentColor" strokeWidth="1.4" />
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

export const CloseIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}><path d="m6 6 12 12M18 6 6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></Icon>
);

export const RouteIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}><path d="M5 4v4a4 4 0 0 0 4 4h10M5 20v-4a4 4 0 0 1 4-4M16 9l3 3-3 3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></Icon>
);

export const CopyIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}><rect x="8" y="8" width="11" height="11" rx="2" stroke="currentColor" strokeWidth="1.7" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" stroke="currentColor" strokeWidth="1.7" /></Icon>
);

export const MoreIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}><circle cx="5" cy="12" r="1.5" fill="currentColor" /><circle cx="12" cy="12" r="1.5" fill="currentColor" /><circle cx="19" cy="12" r="1.5" fill="currentColor" /></Icon>
);
