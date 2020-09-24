from accounts.models import KippoOrganization, PublicHoliday


class OrganizationProjectDataManager:
    """Provides helper methods to easily obtain desired data"""

    def __init__(self, organization: KippoOrganization) -> None:
        self.organization = organization

    def get_assignee_task_load(self):

        max_days = 70
        projects_results, assignee_date_keyed_scheduled_projects_ids, latest_effort_date = get_projects_load(organization, schedule_start_date)
        if not projects_results:
            raise ValueError("(get_projects_load) project_results is empty!")

        country_holidays = defaultdict(list)
        for public_holiday in PublicHoliday.objects.filter(day__gte=schedule_start_date):
            country_holidays[public_holiday.country].append(public_holiday)

        project_data = []
        # prepare data for plotting
        for project_id in projects_results:
            data = {
                # length of columns expected to be the same
                "project_ids": [],
                "project_names": [],
                "current_dates": [],
                "assignee_calendar_days": [],
                "assignees": [],
                "project_assignee_grouped": [],
                "task_ids": [],
                "task_urls": [],
                "task_titles": [],
                "task_estimate_days": [],
                "task_dates": [],
                "descriptions": [],
                "holiday_dates": [],
                "weekend_dates": [],
                "scheduled_dates": [],
                "unscheduled_dates": [],
                "uncommitted_dates": [],
                "personal_holiday_dates": [],
            }
            project_assignee_data = defaultdict(dict)
            project_populated = False
            project_start_date = None
            project_target_date = None
            project_estimate_date = None
            for assignee, assignee_tasks in projects_results[project_id].items():
                if assignee_filter and assignee not in assignee_filter:
                    logger.debug(f"assignee_filter({assignee_filter}) applied, skipping: {assignee}")
                    continue
                (
                    assignee_data,
                    project_start_date,
                    project_target_date,
                    assignee_total_days,
                    assignee_max_task_date,
                    populated,
                ) = _add_assignee_project_data(
                    organization,
                    schedule_start_date,
                    assignee,
                    assignee_tasks,
                    country_holidays,
                    assignee_date_keyed_scheduled_projects_ids,
                    max_days=max_days,
                )
                if populated:
                    project_populated = True
                if not project_estimate_date:
                    project_estimate_date = assignee_max_task_date
                elif project_estimate_date and project_estimate_date < assignee_max_task_date:
                    project_estimate_date = assignee_max_task_date

                for category, values in assignee_data.items():
                    data[category].extend(values)
                project_assignee_data[assignee]["total_scheduled_days"] = assignee_total_days
                project_assignee_data[assignee]["estimated_complete_date"] = assignee_max_task_date

            if project_populated:  # may not be filled if using assignee filter
                project_data.append((project_id, project_start_date, project_target_date, project_estimate_date, data, project_assignee_data))
            else:
                logger.warning(f"No data for Project-id({project_id}): {assignee_filter}")

        # prepare project milestone info
        project_milestones = defaultdict(list)
        project_ids = projects_results.keys()
        for milestone in KippoMilestone.objects.filter(project__id__in=project_ids).order_by("target_date"):
            milestone_info = {
                "project_id": str(milestone.project.id),
                "start_date": milestone.start_date,
                "target_date": milestone.target_date,
                "title": milestone.title,
                "description": milestone.description,
            }
            project_milestones[milestone.project.id].append(milestone_info)

        logger.debug(f"len(project_data)={len(project_data)}")
        logger.debug(f"project_milestones={project_milestones}")
