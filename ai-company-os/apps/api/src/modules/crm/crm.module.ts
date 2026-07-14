import type { RequestContext } from "@aicos/shared";
import { EventBus } from "../../platform/event-bus.js";
import { InMemoryRepository, type Entity } from "../../platform/repository.js";
import type { ToolRegistry } from "../../core/tools/registry.js";

export interface Customer extends Entity {
  name: string;
  email: string;
  status: "lead" | "active" | "churned";
  createdAt: string;
}

export interface Proposal extends Entity {
  customerId: string;
  customerName: string;
  amount: number;
  currency: string;
  status: "draft" | "sent" | "accepted" | "rejected";
  createdAt: string;
}

/** Módulo CRM: domínio + serviço + ferramentas expostas à IA. */
export class CrmModule {
  readonly customers = new InMemoryRepository<Customer>();
  readonly proposals = new InMemoryRepository<Proposal>();

  constructor(private readonly events: EventBus) {}

  createCustomer(ctx: RequestContext, name: string, email: string): Customer {
    const existing = this.customers
      .list(ctx.tenantId)
      .find((c) => c.name.toLowerCase() === name.toLowerCase());
    if (existing) return existing;
    const customer = this.customers.create(ctx.tenantId, {
      name,
      email,
      status: "lead",
      createdAt: new Date().toISOString(),
    });
    void this.events.publish("crm.customer.created", ctx.tenantId, customer);
    return customer;
  }

  findCustomerByName(ctx: RequestContext, name: string): Customer | undefined {
    return this.customers
      .list(ctx.tenantId)
      .find((c) => c.name.toLowerCase().includes(name.toLowerCase()));
  }

  createProposal(ctx: RequestContext, customerName: string, amount: number): Proposal {
    let customer = this.findCustomerByName(ctx, customerName);
    if (!customer) customer = this.createCustomer(ctx, customerName, "");
    const proposal = this.proposals.create(ctx.tenantId, {
      customerId: customer.id,
      customerName: customer.name,
      amount,
      currency: "BRL",
      status: "sent",
      createdAt: new Date().toISOString(),
    });
    void this.events.publish("crm.proposal.sent", ctx.tenantId, proposal);
    return proposal;
  }

  register(registry: ToolRegistry): void {
    registry.register({
      name: "crm.create_customer",
      description: "Cadastra um novo cliente/lead no CRM.",
      params: {
        name: { type: "string", description: "Nome do cliente", required: true },
        email: { type: "string", description: "Email do cliente" },
      },
      allowedRoles: ["owner", "admin", "manager", "member"],
      handler: (ctx, input) => {
        const c = this.createCustomer(ctx, String(input.name), String(input.email ?? ""));
        return {
          ok: true,
          summary: `Cliente "${c.name}" cadastrado no CRM${c.email ? ` (${c.email})` : ""}.`,
          data: { kind: "customer", title: "Cliente cadastrado", payload: c },
        };
      },
    });

    registry.register({
      name: "crm.list_customers",
      description: "Lista os clientes do CRM.",
      params: {},
      handler: (ctx) => {
        const list = this.customers.list(ctx.tenantId);
        return {
          ok: true,
          summary: `Você tem ${list.length} cliente(s) no CRM.`,
          data: { kind: "table", title: "Clientes", payload: list },
        };
      },
    });

    registry.register({
      name: "crm.create_proposal",
      description: "Cria e envia uma proposta/orçamento para um cliente.",
      params: {
        customer: { type: "string", description: "Nome do cliente", required: true },
        amount: { type: "number", description: "Valor da proposta em BRL", required: true },
      },
      allowedRoles: ["owner", "admin", "manager", "member"],
      handler: (ctx, input) => {
        const p = this.createProposal(ctx, String(input.customer), Number(input.amount));
        return {
          ok: true,
          summary: `Proposta de R$ ${p.amount.toLocaleString("pt-BR")} enviada para ${p.customerName}.`,
          data: { kind: "proposal", title: "Proposta enviada", payload: p },
        };
      },
    });
  }
}
